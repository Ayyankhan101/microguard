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
from ..signals import EMPTY_SIGNALS, Signals


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
    # Float epochs, NOT datetimes. features.Session keeps the same two names as
    # datetimes taken from entry.timestamp; both expose a float .duration, so
    # the two shapes duck-type. Anything rebuilding a LiveSession from stored
    # entries must convert with .timestamp() — a raw datetime here makes
    # .duration return a timedelta and breaks labeler.py's MIN_RATE_WINDOW_S
    # comparison.
    start_time: float | None = None  # time.time() epoch
    end_time: float | None = None

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


@dataclass(frozen=True)
class SessionSnapshot:
    """Everything one actor's storage knows, read in a single round trip.

    `record_request` returns this rather than a bare LiveSession because the
    signals resolved for an actor are needed by the same scoring call, and
    fetching them separately would mean a second Redis round trip on the one
    path that runs for every visitor request behind nginx `auth_request`. On a
    remote Redis that is the difference between one 15ms wait and two.

    `signals` defaults to EMPTY_SIGNALS, which reads as "not looked up" rather
    than "looked up and clean" — the distinction every signal rule depends on.
    """

    session: LiveSession
    signals: Signals = EMPTY_SIGNALS


@runtime_checkable
class SessionStateStore(Protocol):
    """Protocol for session state backends.

    One write method, deliberately. An earlier get()/set() pair forced callers
    into a read-modify-write across two round trips, which lost concurrent
    appends from the same actor: a 20-thread test against real Redis kept only
    9 of 20 requests. Recording and reading back must happen in one atomic
    operation, so they are one method.

    Implementations must be thread-safe and support TTL-based expiry.
    """

    def record_request(
        self,
        ip: str,
        user_agent: str,
        entry: LogEntry,
        ttl_seconds: int | None = None,
    ) -> SessionSnapshot:
        """Atomically append entry and return this actor's full state.

        Creates the session if absent, refreshes its TTL, and caps its history.
        The returned snapshot holds the post-append session, ready to score,
        and whatever signals were already resolved for this actor.
        """
        ...

    def delete(self, ip: str) -> None:
        """Explicitly remove a session."""
        ...
