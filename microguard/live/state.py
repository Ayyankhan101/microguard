"""Session state interface for live bot detection.

This module defines a Protocol for session state storage and a concrete
in-memory implementation for testing. The production implementation lives
in redis_store.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..parser import LogEntry


@dataclass
class LiveSession:
    """A live session tracking requests in real-time.

    Duck-types the Session interface from features.py (has .requests,
    .ip, .user_agent, .start_time, .end_time, .add_request(), and
    .request_count) so extract_features() and label_session() work
    without changes.
    """

    ip: str
    user_agent: str = ""
    requests: list[LogEntry] = field(default_factory=list)
    start_time: float | None = None  # time.time() epoch
    end_time: float | None = None
    last_score: float | None = None

    def add_request(self, entry: LogEntry) -> None:
        """Add a request to the session."""
        self.requests.append(entry)
        now = time.time()
        if self.start_time is None:
            self.start_time = now
        self.end_time = now

    @property
    def request_count(self) -> int:
        return len(self.requests)

    @property
    def duration(self) -> float:
        if self.start_time is not None and self.end_time is not None:
            return self.end_time - self.start_time
        return 0.0


@runtime_checkable
class SessionStateStore(Protocol):
    """Protocol for session state backends.

    Implementations must be thread-safe and support TTL-based expiry.
    """

    def get(self, key: str) -> LiveSession | None:
        """Return the session for key, or None if expired/absent."""
        ...

    def set(self, key: str, session: LiveSession, ttl_seconds: int = 1800) -> None:
        """Store a session with TTL."""
        ...

    def delete(self, key: str) -> None:
        """Explicitly remove a session."""
        ...

    def incr_score(self, key: str, amount: float = 1.0) -> float:
        """Atomically increment the score for a session, returning new value."""
        ...

    def get_score(self, key: str) -> float:
        """Return the current score for a session, or 0.0 if absent."""
        ...

    def keys(self, pattern: str = "live:*") -> list[str]:
        """Return keys matching the pattern."""
        ...
