"""AbuseIPDB — the one threat-intel source that needs a key.

Optional by design. With no `ABUSEIPDB_API_KEY` set, `is_configured()` is False
and `check_ip()` returns None for everything: the signal simply never fires,
and nothing about the install is broken. That has to be the default, because a
security tool that stops working until you sign up for a third-party account
is a security tool people stop running.

Runs only inside `microguard signals`, never on the request path. It is the
slowest thing in this project -- a real HTTP round trip to someone else's
service -- and nginx turns a slow `auth_request` into a 500 for the visitor.

Two budgets are respected, not one. The response cache stops repeat lookups of
the same address, and a daily counter stops the process burning through the
free tier's 1000 checks: exhausting it gets the key rate-limited, which takes
the signal down for every address rather than for one.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

API_URL = "https://api.abuseipdb.com/api/v2/check"
FREE_TIER_DAILY_LIMIT = 1000
DEFAULT_TTL_S = 86400
REQUEST_TIMEOUT_S = 10


def _utc_day() -> str:
    """The quota day, in UTC.

    AbuseIPDB's budget rolls on UTC midnight. Using the local date would reset
    the local counter hours early or late, which either wastes budget or
    overruns it -- and overrunning gets the key rate-limited, taking the signal
    down for every address rather than one.
    """
    return datetime.now(timezone.utc).date().isoformat()


def _http_check(ip: str, api_key: str) -> float:  # pragma: no cover - the network call
    query = urllib.parse.urlencode({"ipAddress": ip, "maxAgeInDays": 90})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"Key": api_key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return float(payload["data"]["abuseConfidenceScore"])


class AbuseIPDBClient:
    """Cached, budgeted lookups. Never raises into the caller."""

    def __init__(
        self,
        api_key: str | None = None,
        cache_path: Path | None = None,
        ttl: int = DEFAULT_TTL_S,
        daily_limit: int = FREE_TIER_DAILY_LIMIT,
        fetch: Callable[[str, str], float] | None = None,
    ):
        self._api_key = (api_key if api_key is not None else os.environ.get("ABUSEIPDB_API_KEY", "")).strip()
        self._cache_path = Path(cache_path) if cache_path else None
        self._ttl = ttl
        self._daily_limit = daily_limit
        self._fetch = fetch or _http_check
        self._cache = self._load_cache()
        self._quota_day = _utc_day()
        self.quota_used = 0
        self.last_error = ""

    @property
    def quota_remaining(self) -> int:
        self._roll_day()
        return max(0, self._daily_limit - self.quota_used)

    def is_configured(self) -> bool:
        """Whether a key is present. An absent key is normal, not an error."""
        return bool(self._api_key)

    def check_ip(self, ip: str) -> float | None:
        """The abuse confidence score 0-100, or None.

        None means "no opinion" and is returned for an unconfigured client, an
        exhausted budget, and any failed lookup. It is deliberately NOT zero:
        zero is a real score meaning "reported clean", and recording a failure
        as one would let an unreachable API quietly vouch for every address it
        could not check.
        """
        if not self.is_configured():
            return None

        cached = self._cache.get(ip)
        if cached and (time.time() - cached.get("ts", 0)) < self._ttl:
            return float(cached["score"])

        self._roll_day()
        if self.quota_used >= self._daily_limit:
            self.last_error = "daily quota exhausted"
            return None

        try:
            score = float(self._fetch(ip, self._api_key))
        except Exception as exc:  # noqa: BLE001 - every failure degrades the same way
            # Not cached: a transient outage must not suppress lookups for this
            # address for the next day.
            self.last_error = str(exc)
            logger.warning("abuseipdb lookup for %s failed: %s", ip, exc)
            return None

        self.quota_used += 1
        self.last_error = ""
        self._cache[ip] = {"score": score, "ts": time.time()}
        self._save_cache()
        return score

    def _roll_day(self) -> None:
        today = _utc_day()
        if today != self._quota_day:
            self._quota_day = today
            self.quota_used = 0

    def _load_cache(self) -> dict:
        if not self._cache_path or not self._cache_path.exists():
            return {}
        try:
            loaded = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("abuseipdb cache at %s unreadable, starting empty", self._cache_path)
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache), encoding="utf-8")
        except OSError:
            # A cache we cannot persist is a cache we do without. The score is
            # already in hand and in memory.
            logger.warning("could not write abuseipdb cache at %s", self._cache_path)
