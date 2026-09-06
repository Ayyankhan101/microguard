"""Redis-backed session state store for live bot detection.

Stores LiveSession objects as JSON blobs with per-session TTL keys.
Uses redis-py with connection pooling for thread-safety.
"""

from __future__ import annotations

import json
import time

import redis

from .state import LiveSession

# Type alias for redis client — redis-py's client type hierarchy is complex
# and version-dependent; duck-typing is simpler here.
RedisClient = redis.Redis  # type: ignore[type-arg]


class RedisSessionStateStore:
    """Redis-backed implementation of SessionStateStore.

    Key schema:
        live:{ip}              → JSON blob of LiveSession
        live:{ip}:score        → float (bot score accumulator)
        live:{ip}:score:ts     → timestamp of last score update

    All keys share the same TTL so sessions expire together.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        prefix: str = "live:",
        default_ttl: int = 1800,
    ):
        self._r = redis_client
        self._prefix = prefix
        self._default_ttl = default_ttl

    def _session_key(self, ip: str) -> str:
        return f"{self._prefix}{ip}"

    def _score_key(self, ip: str) -> str:
        return f"{self._prefix}{ip}:score"

    def _score_ts_key(self, ip: str) -> str:
        return f"{self._prefix}{ip}:score:ts"

    def get(self, key: str) -> LiveSession | None:
        """Deserialize a LiveSession from Redis, or return None."""
        ip = key.removeprefix(self._prefix)
        raw = self._r.get(self._session_key(ip))  # type: ignore[return-value]
        if raw is None:
            return None
        try:
            data = json.loads(raw)  # type: ignore[arg-type]
        except (json.JSONDecodeError, TypeError):
            return None
        session = LiveSession(ip=data["ip"], user_agent=data.get("user_agent", ""))
        session.start_time = data.get("start_time")
        session.end_time = data.get("end_time")
        session.last_score = data.get("last_score")
        # Reconstruct LogEntry objects from serialized form
        for entry_data in data.get("requests", []):
            from datetime import datetime

            entry = type(
                "LogEntry",
                (),
                {
                    "ip": entry_data["ip"],
                    "timestamp": datetime.fromisoformat(entry_data["timestamp"]),
                    "method": entry_data["method"],
                    "url": entry_data["url"],
                    "status": entry_data["status"],
                    "size": entry_data["size"],
                    "referer": entry_data.get("referer", ""),
                    "user_agent": entry_data.get("user_agent", ""),
                    "raw_line": entry_data.get("raw_line", ""),
                },
            )()
            session.requests.append(entry)  # type: ignore[arg-type]
        return session

    def set(self, key: str, session: LiveSession, ttl_seconds: int | None = None) -> None:
        """Serialize and store a LiveSession with TTL."""
        ip = key.removeprefix(self._prefix)
        ttl = ttl_seconds or self._default_ttl

        data = {
            "ip": session.ip,
            "user_agent": session.user_agent,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "last_score": session.last_score,
            "requests": [
                {
                    "ip": e.ip,
                    "timestamp": e.timestamp.isoformat(),
                    "method": e.method,
                    "url": e.url,
                    "status": e.status,
                    "size": e.size,
                    "referer": e.referer,
                    "user_agent": e.user_agent,
                    "raw_line": getattr(e, "raw_line", ""),
                }
                for e in session.requests
            ],
        }

        pipe = self._r.pipeline()
        pipe.set(self._session_key(ip), json.dumps(data), ex=ttl)
        pipe.set(self._score_ts_key(ip), str(time.time()), ex=ttl)
        pipe.execute()

    def delete(self, key: str) -> None:
        ip = key.removeprefix(self._prefix)
        pipe = self._r.pipeline()
        pipe.delete(self._session_key(ip))
        pipe.delete(self._score_key(ip))
        pipe.delete(self._score_ts_key(ip))
        pipe.execute()

    def incr_score(self, key: str, amount: float = 1.0) -> float:
        """Atomically increment score, returning new value."""
        ip = key.removeprefix(self._prefix)
        score_key = self._score_key(ip)
        pipe = self._r.pipeline()
        pipe.incrbyfloat(score_key, amount)
        pipe.expire(score_key, self._default_ttl)
        pipe.execute()
        return float(self._r.get(score_key) or 0.0)  # type: ignore[arg-type]

    def get_score(self, key: str) -> float:
        ip = key.removeprefix(self._prefix)
        val = self._r.get(self._score_key(ip))  # type: ignore[return-value]
        return float(val) if val else 0.0  # type: ignore[arg-type]

    def keys(self, pattern: str = "live:*") -> list[str]:
        return [k.decode() for k in self._r.keys(pattern)]  # type: ignore[union-attr]
