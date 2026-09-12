"""Shared fixtures and guards for the live/ suite.

`microguard/live/__init__.py` raises ImportError when the `live` extra is not
installed, and every module in this directory imports `microguard.live.*` at
import time. Without the guard below that ImportError surfaces as a pytest
COLLECTION ERROR rather than a skip, so a base `pip install -e .` turns the
whole directory red instead of quietly stepping over it.

CI installs the extra (see .github/workflows/test.yml) so these tests really
run there; the guard only covers a bare local checkout.
"""

from dataclasses import replace

import pytest

pytest.importorskip(
    "redis",
    reason="live/ requires the 'live' extra: pip install -e '.[live]'",
)

from microguard.live.redis_store import MAX_SESSION_ENTRIES
from microguard.live.state import LiveSession, SessionSnapshot
from microguard.parser import LogEntry
from microguard.signals import EMPTY_SIGNALS, Signals


class InMemoryStore:
    """SessionStateStore double for tests that don't need a real Redis.

    Mirrors RedisSessionStateStore's observable behavior: atomic append, the
    same history cap, the same clock derivation (start/end come from the
    RETAINED entries as float epochs, so the cap behaves as a sliding window
    and `.duration` stays a float), and the same SessionSnapshot return shape.
    A double that drifts from those semantics would hide the bugs they exist to
    prevent — tests/live/test_snapshot.py asserts the two agree.

    Redis-level behavior — pipelines, TTL expiry, key versioning — is covered
    against a real server in test_redis_store.py, not here.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._signals: dict[str, Signals] = {}

    def record_request(
        self,
        ip: str,
        user_agent: str,
        entry: LogEntry,
        ttl_seconds: int | None = None,
    ) -> SessionSnapshot:
        session = self._sessions.get(ip)
        if session is None:
            session = LiveSession(ip=ip, user_agent=user_agent)
            self._sessions[ip] = session
        session.requests.append(entry)
        del session.requests[:-MAX_SESSION_ENTRIES]
        session.start_time = session.requests[0].timestamp.timestamp()
        session.end_time = session.requests[-1].timestamp.timestamp()
        # fp_resolved mirrors the real store: the pipeline always reads the
        # fingerprint key, so the read happened whether or not it found one.
        # A double that reported False here would let rule 1's batch guard
        # pass in tests while failing in production.
        return SessionSnapshot(
            session=session,
            signals=replace(self._signals.get(ip, EMPTY_SIGNALS), fp_resolved=True),
        )

    def delete(self, ip: str) -> None:
        self._sessions.pop(ip, None)

    # --- test-only, not part of SessionStateStore ---

    def peek(self, ip: str) -> LiveSession | None:
        """Read a session back without recording anything."""
        return self._sessions.get(ip)

    def set_signals(self, ip: str, signals: Signals) -> None:
        """Stand in for the refresher having resolved signals for this actor."""
        self._signals[ip] = signals


@pytest.fixture()
def store():
    return InMemoryStore()


@pytest.fixture(autouse=True)
def _never_touch_the_real_cache(tmp_path, monkeypatch):
    """Keep feed caches out of the developer's real ~/.cache.

    `build_sources(cache=None)` resolves to `cache_dir()`, which is the right
    production default and the wrong thing for a test: a run would write a
    two-line fixture feed into the user's cache, and the NEXT run would read it
    back as fresh and skip the injected fetch entirely. That is how a passing
    suite starts hiding a broken source.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
