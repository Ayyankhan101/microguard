"""Redis-backed session state store for live bot detection.

Stores each actor's session as a capped Redis LIST of serialized LogEntry
payloads. Requires `pip install microguard[live]`.

    Key schema
        live:v2:{ip}   LIST of JSON LogEntry payloads, newest last

    One append == one pipeline, four commands, one round trip:

        RPUSH  live:v2:{ip}      <entry json>   append
        LTRIM  live:v2:{ip}      -200 -1        cap history
        EXPIRE live:v2:{ip}      <ttl>          slide the expiry window
        LRANGE live:v2:{ip}      0 -1           read back for scoring
        GET    mg:v1:signals:{ip}               resolved signals, same trip

    The signals GET rides along in the same pipeline rather than being its own
    call. Scoring needs both, and this runs for every request behind nginx
    `auth_request`, so a second round trip would double the dominant cost of
    the whole path on any Redis that is not on localhost.

Why a LIST and not a JSON blob (the v1 shape):

    The blob forced callers into GET, mutate in Python, SET — two round trips
    with a gap in between. Concurrent requests from one actor both read the
    same session and the second write erased the first. Measured against a real
    Redis with 20 concurrent appends: 9 survived. Undercounting request_count
    weakens exactly the timing and volume rules that matter under load. RPUSH
    is atomic, so the gap does not exist.

Why the key prefix carries a version:

    v1 keys hold strings. RPUSH against a string is WRONGTYPE, so sharing the
    key name would break every in-flight session on deploy — silently, since
    the entrypoints fail open — and again in reverse on rollback. v2 keys are
    a separate namespace; v1 keys are never touched and expire on their own
    TTL. Bump this whenever the value shape changes.
"""

from __future__ import annotations

import json

import redis

from ..parser import LogEntry
from ..signals import EMPTY_SIGNALS, signals_from_payload
from .state import LiveSession, SessionSnapshot

# Type alias for redis client — redis-py's client type hierarchy is complex
# and version-dependent; duck-typing is simpler here.
RedisClient = redis.Redis  # type: ignore[type-arg]

# Upper bound on retained requests per session. Bounds Redis memory against a
# session that never expires naturally, and bounds per-request CPU: scoring
# re-reads and re-features the whole retained list, at roughly 2ms per request
# at this size. This is the tuning lever if that cost matters.
MAX_SESSION_ENTRIES = 200

# The default key prefix, named so read-only tools (explain) can reach a
# session without constructing a store that would record one.
SESSION_PREFIX_DEFAULT = "live:v2:"


def _session_from(ip: str, user_agent: str, raw_entries: list) -> LiveSession:
    """Rebuild a LiveSession from the retained slice of its history.

    The clock comes from the RETAINED entries, which makes the LTRIM cap a
    sliding window rather than a truncation. That matters for the rate rule:
    once request_count is pinned at MAX_SESSION_ENTRIES, a clock anchored to
    the session's original start would let duration grow while the numerator
    stayed frozen, so a sustained flood would look slower the longer it ran
    (200 requests over 10s reads as 1200/min and blocks; the same flood at 30
    minutes reads as 6.7/min and is allowed). Anchored to what is retained,
    the rate stays honest.

    `.timestamp()` is not optional: LiveSession.start_time/end_time are float
    epochs. Assigning the entries' datetimes directly makes .duration return a
    timedelta, which raises in labeler.py's MIN_RATE_WINDOW_S comparison.
    """
    session = LiveSession(ip=ip, user_agent=user_agent)
    for raw in raw_entries:
        try:
            session.requests.append(LogEntry.from_dict(json.loads(raw)))
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            # One unreadable entry must not discard the whole session; scoring
            # a slightly shorter history beats scoring none of it.
            continue
    if session.requests:
        session.start_time = session.requests[0].timestamp.timestamp()
        session.end_time = session.requests[-1].timestamp.timestamp()
    return session


def _signals_from(raw: str | None):
    """Decode the stored signal payload, or report nothing was resolved.

    Every failure path returns EMPTY_SIGNALS rather than raising. This runs on
    the request path, and a signal that cannot be read is a missing signal, not
    a reason to stop scoring.
    """
    if not raw:
        return EMPTY_SIGNALS
    try:
        return signals_from_payload(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return EMPTY_SIGNALS


class RedisSessionStateStore:
    """Redis-backed implementation of SessionStateStore."""

    def __init__(
        self,
        redis_client: RedisClient,
        prefix: str = SESSION_PREFIX_DEFAULT,
        default_ttl: int = 1800,
        signals_prefix: str = "mg:v1:signals:",
    ):
        self._r = redis_client
        self._prefix = prefix
        self._default_ttl = default_ttl
        self._signals_prefix = signals_prefix

    def _session_key(self, ip: str) -> str:
        return f"{self._prefix}{ip}"

    def _signals_key(self, ip: str) -> str:
        return f"{self._signals_prefix}{ip}"

    def record_request(
        self,
        ip: str,
        user_agent: str,
        entry: LogEntry,
        ttl_seconds: int | None = None,
    ) -> SessionSnapshot:
        """Atomically append entry and return this actor's full state."""
        key = self._session_key(ip)
        ttl = ttl_seconds or self._default_ttl

        pipe = self._r.pipeline()
        pipe.rpush(key, json.dumps(entry.to_dict()))
        pipe.ltrim(key, -MAX_SESSION_ENTRIES, -1)
        pipe.expire(key, ttl)
        pipe.lrange(key, 0, -1)
        pipe.get(self._signals_key(ip))
        *_, raw_entries, raw_signals = pipe.execute()

        return SessionSnapshot(
            session=_session_from(ip, user_agent, raw_entries),
            signals=_signals_from(raw_signals),
        )

    def delete(self, ip: str) -> None:
        """Explicitly remove a session."""
        self._r.delete(self._session_key(ip))
