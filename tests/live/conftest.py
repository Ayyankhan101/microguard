"""Shared fixtures and guards for the live/ suite.

`microguard/live/__init__.py` raises ImportError when the `live` extra is not
installed, and every module in this directory imports `microguard.live.*` at
import time. Without the guard below that ImportError surfaces as a pytest
COLLECTION ERROR rather than a skip, so a base `pip install -e .` turns the
whole directory red instead of quietly stepping over it.

CI installs the extra (see .github/workflows/test.yml) so these tests really
run there; the guard only covers a bare local checkout.
"""

import pytest

pytest.importorskip(
    "redis",
    reason="live/ requires the 'live' extra: pip install -e '.[live]'",
)

from microguard.live.redis_store import MAX_SESSION_ENTRIES
from microguard.live.state import LiveSession
from microguard.parser import LogEntry


class InMemoryStore:
    """SessionStateStore double for tests that don't need a real Redis.

    Mirrors RedisSessionStateStore's observable behavior: atomic append, the
    same history cap, and the same clock derivation (start/end come from the
    RETAINED entries as float epochs, so the cap behaves as a sliding window
    and `.duration` stays a float). A double that drifts from those semantics
    would hide the bugs they exist to prevent.

    Redis-level behavior — pipelines, TTL expiry, key versioning — is covered
    against a real server in test_redis_store.py, not here.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}

    def record_request(
        self,
        ip: str,
        user_agent: str,
        entry: LogEntry,
        ttl_seconds: int | None = None,
    ) -> LiveSession:
        session = self._sessions.get(ip)
        if session is None:
            session = LiveSession(ip=ip, user_agent=user_agent)
            self._sessions[ip] = session
        session.requests.append(entry)
        del session.requests[:-MAX_SESSION_ENTRIES]
        session.start_time = session.requests[0].timestamp.timestamp()
        session.end_time = session.requests[-1].timestamp.timestamp()
        return session

    def delete(self, ip: str) -> None:
        self._sessions.pop(ip, None)

    # --- test-only, not part of SessionStateStore ---

    def peek(self, ip: str) -> LiveSession | None:
        """Read a session back without recording anything."""
        return self._sessions.get(ip)


@pytest.fixture()
def store():
    return InMemoryStore()
