"""`microguard signals` — the slow-tier process.

Builds the feed sources, then loops: resolve every live actor, stamp the
heartbeat, sleep. Kept separate from signals_refresher.py so one pass stays
testable without a loop around it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import redis

from .signals_refresher import (
    DEFAULT_INTERVAL_S,
    SignalRefresher,
    SourceHealth,
    cache_dir,
    load_cidr_ranges,
    load_tor_exit_nodes,
)

logger = logging.getLogger(__name__)


def build_sources(
    cache: Path | None = None,
    tor_fetch: Callable[[], str] | None = None,
    cidr_fetch: Callable[[], str] | None = None,
) -> tuple[set[str], list, list[SourceHealth]]:
    """Load every feed, reporting what each one did.

    A feed that fails degrades to empty or to its stale cache and is recorded
    as unhealthy. It never raises: a broken feed must cost one signal, not the
    whole refresher.
    """
    root = Path(cache) if cache is not None else cache_dir()
    health: list[SourceHealth] = []

    errors: dict[str, str] = {}
    tor = load_tor_exit_nodes(
        root / "tor_exit_nodes.txt", fetch=tor_fetch,
        on_error=lambda e: errors.__setitem__("tor", e),
    )
    ranges = load_cidr_ranges(
        root / "hosting_ranges.json", fetch=cidr_fetch,
        on_error=lambda e: errors.__setitem__("hosting", e),
    )
    now = time.time()
    health.append(SourceHealth("tor", "tor" not in errors, len(tor), now, errors.get("tor", "")))
    health.append(
        SourceHealth("hosting", "hosting" not in errors, len(ranges), now, errors.get("hosting", ""))
    )
    return tor, ranges, health


def run_refresher(
    client: redis.Redis,
    interval: int = DEFAULT_INTERVAL_S,
    session_ttl: int = 1800,
    cache: Path | None = None,
    tor_fetch: Callable[[], str] | None = None,
    cidr_fetch: Callable[[], str] | None = None,
    _max_iterations: int | None = None,
) -> None:
    """Resolve signals forever. `_max_iterations` is a test-only seam."""
    iterations = 0
    while _max_iterations is None or iterations < _max_iterations:
        iterations += 1
        try:
            tor, ranges, health = build_sources(cache, tor_fetch, cidr_fetch)
            resolved = SignalRefresher(
                client, tor_nodes=tor, hosting_ranges=ranges,
                ttl=session_ttl, health=health,
            ).run_once()
            logger.info("resolved signals for %d actor(s)", resolved)
        except Exception:
            # One bad pass must not end the process. A refresher that exits
            # leaves the check server reading records that silently age out,
            # with nothing saying why the signal stopped.
            logger.exception("refresh pass failed, retrying next interval")
        if _max_iterations is None or iterations < _max_iterations:
            time.sleep(interval)


def main(
    redis_url: str = "redis://localhost:6379",
    interval: int = DEFAULT_INTERVAL_S,
    session_ttl: int = 1800,
) -> None:
    """Entry point for `microguard signals`."""
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    client.ping()
    print(f"microguard signals refresher: every {interval}s")
    print(f"  redis: {redis_url}")
    print(f"  cache: {cache_dir()}")
    print("  resolves signals for actors with a live session only")
    tor, ranges, health = build_sources()
    for source in health:
        state = f"{source.entries} entries" if source.ok else f"FAILED ({source.error})"
        print(f"  {source.name}: {state}")
    del tor, ranges
    try:
        run_refresher(client, interval=interval, session_ttl=session_ttl)
    except KeyboardInterrupt:
        print("\nShutting down.")


def heartbeat(client: redis.Redis) -> dict | None:
    """The last recorded pass, or None if the refresher has never run."""
    from .signals_refresher import HEARTBEAT_KEY

    raw = cast("str | None", client.get(HEARTBEAT_KEY))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
