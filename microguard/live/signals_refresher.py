"""The slow tier — resolving external signals out of the request path.

Everything here runs in its own process (`microguard signals`). Nothing in it
is ever called while a visitor waits. That separation is the point:

    microguard signals            microguard serve
      │                             │
      ├─ fetch feeds (network)      ├─ GET mg:v1:signals:{ip}   (one Redis read,
      ├─ SCAN live:v2:*             │     already inside the session pipeline)
      ├─ SET mg:v1:signals:{ip}     └─ score, answer nginx
      └─ SET mg:v1:signals:heartbeat

If this process dies, signal records go stale and then expire, and the check
server reads nothing and scores without them. That is a degraded signal, not an
outage. Putting these fetches inside the check server would make a slow feed a
500 for every visitor, because nginx turns a slow `auth_request` into one.

Records are written only for actors that already have a live session, so the
keyspace is bounded by real traffic rather than by the size of the internet,
and each record expires on its own so it cannot outlive the session it
describes.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import redis

logger = logging.getLogger(__name__)

# Published, keyless, documented endpoints. Both are fetched rather than
# checked in: a CIDR list typed from memory into a security tool is wrong in
# ways nobody notices, and a stale checked-in copy is worse than a cached one
# with a known age.
TOR_EXIT_LIST_URL = "https://check.torproject.org/torbulkexitlist"
AWS_IP_RANGES_URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"

HEARTBEAT_KEY = "mg:v1:signals:heartbeat"
SIGNALS_PREFIX = "mg:v1:signals:"
SESSION_PREFIX = "live:v2:"

DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_INTERVAL_S = 300
FETCH_TIMEOUT_S = 30


@dataclass(frozen=True)
class SourceHealth:
    """What a feed looked like on its last attempt.

    Every source here fails silently by design — a feed that cannot be fetched
    simply stops contributing. That is the right runtime behavior and the wrong
    operator experience, so the outcome is recorded and surfaced rather than
    only logged.
    """

    name: str
    ok: bool
    entries: int
    fetched_at: float
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "entries": self.entries,
            "fetched_at": self.fetched_at,
            "error": self.error,
        }


def cache_dir() -> Path:
    """Where fetched feeds are cached.

    Not inside the package. `data/` ships with the wheel and is read-only on a
    normal install, so writing a cache there fails exactly where it is hardest
    to notice: a root-owned or system install.
    """
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "microguard"


def _fetch(url: str) -> str:  # pragma: no cover - the one real network call
    """Fetch a feed body. Every caller can inject a replacement, which is how
    the cache-and-degrade behavior around it is tested without the network."""
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as response:
        return response.read().decode("utf-8", errors="replace")


def _is_fresh(path: Path, max_age_hours: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    except OSError:
        return False


def _read_cache(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_cache(path: Path, body: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError:
        # A cache we cannot write is a cache we do without. The fetched value
        # is already in hand; refusing to use it over a disk problem would
        # trade a working signal for none.
        logger.warning("could not write feed cache at %s", path, exc_info=True)


def _body_for(
    cache_path: Path,
    max_age_hours: int,
    fetch: Callable[[], str],
) -> tuple[str | None, str]:
    """The freshest body available, and why, without ever raising.

    Order matters: a fresh cache short-circuits the fetch entirely, so a
    refresher restarted in a loop does not hammer a public feed.
    """
    if _is_fresh(cache_path, max_age_hours):
        cached = _read_cache(cache_path)
        if cached is not None:
            return cached, ""
    try:
        body = fetch()
    except Exception as exc:  # noqa: BLE001 - any failure degrades the same way
        cached = _read_cache(cache_path)
        if cached is not None:
            logger.warning("feed fetch failed, using stale cache at %s: %s", cache_path, exc)
            return cached, str(exc)
        logger.warning("feed fetch failed and no cache exists at %s: %s", cache_path, exc)
        return None, str(exc)
    _write_cache(cache_path, body)
    return body, ""


def load_tor_exit_nodes(
    cache_path: Path,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    fetch: Callable[[], str] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> set[str]:
    """Current Tor exit-node addresses, or the last set that was readable.

    Never raises. `on_error` is how a caller learns the fetch failed, since the
    return value cannot distinguish a failed feed from a genuinely empty one --
    and an operator needs to see that difference.
    """
    body, error = _body_for(
        Path(cache_path), max_age_hours, fetch or (lambda: _fetch(TOR_EXIT_LIST_URL))
    )
    if error and on_error:
        on_error(error)
    if body is None:
        return set()
    return {
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def load_cidr_ranges(
    cache_path: Path,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    fetch: Callable[[], str] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> list:
    """Datacenter CIDR blocks from an AWS-style prefix document.

    One unparseable prefix skips that prefix, not the document. A third-party
    feed adding a field or a malformed row should cost one range, not all of
    them.
    """
    body, error = _body_for(
        Path(cache_path), max_age_hours, fetch or (lambda: _fetch(AWS_IP_RANGES_URL))
    )
    if error and on_error:
        on_error(error)
    if body is None:
        return []
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("prefix document at %s is not JSON", cache_path)
        return []

    raw: list[str] = []
    for key, field_name in (("prefixes", "ip_prefix"), ("ipv6_prefixes", "ipv6_prefix")):
        for row in document.get(key) or []:
            if isinstance(row, dict) and field_name in row:
                raw.append(row[field_name])

    ranges = []
    for prefix in raw:
        try:
            ranges.append(ipaddress.ip_network(prefix, strict=False))
        except ValueError:
            continue
    return ranges


@dataclass
class SignalRefresher:
    """One resolution pass over every actor with a live session."""

    client: redis.Redis
    tor_nodes: set[str]
    hosting_ranges: Sequence
    ttl: int = 1800
    health: Iterable[SourceHealth] = field(default_factory=tuple)

    def run_once(self) -> int:
        """Resolve and write signals for every live actor. Returns the count."""
        resolved = 0
        for ip in self._live_actors():
            payload = self._resolve(ip)
            if payload is None:
                continue
            self.client.set(f"{SIGNALS_PREFIX}{ip}", json.dumps(payload), ex=self.ttl)
            resolved += 1
        self._beat(resolved)
        return resolved

    def _live_actors(self) -> Iterable[str]:
        """Actors with a session, read with SCAN rather than KEYS.

        KEYS blocks the whole server for the length of the scan, and this runs
        against the same Redis the check server is using to answer nginx.
        """
        for key in self.client.scan_iter(match=f"{SESSION_PREFIX}*", count=200):
            yield key[len(SESSION_PREFIX):]

    def _resolve(self, ip: str) -> dict | None:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            # Session keys are IP-derived, but a stray key in the namespace
            # must not stop the pass for every other actor.
            logger.debug("skipping unparseable actor key: %r", ip)
            return None
        return {
            "tor_exit": ip in self.tor_nodes,
            "hosting_range": any(address in net for net in self.hosting_ranges),
        }

    def _beat(self, resolved: int) -> None:
        """Stamp liveness, with no expiry.

        An absent heartbeat must mean "never ran". If this key expired on its
        own, a refresher that died an hour ago would look identical to one that
        was never started, and that is precisely the difference an operator
        needs to see.
        """
        self.client.set(
            HEARTBEAT_KEY,
            json.dumps({
                "ts": time.time(),
                "resolved": resolved,
                "sources": [s.as_dict() for s in self.health],
            }),
        )
