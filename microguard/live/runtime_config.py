"""Operator-settable live configuration, shared through Redis.

Tuning the block threshold today means editing a `microguard serve` flag and
restarting, which drops every in-flight session — exactly the state the timing
and rate rules depend on. This stores the threshold beside the session data so
the dashboard can move it and the running scorer picks it up.

    Key schema

        mg:v1:config   HASH  block_threshold -> float (absent = no override)

The value is cached in-process for a few seconds. The scorer reads it once per
request, and the check server handles every request to the protected site; an
uncached read would add a Redis round trip to that path for a value that
changes a few times a day.
"""

from __future__ import annotations

import logging
import time

import redis

logger = logging.getLogger(__name__)

RedisClient = redis.Redis  # type: ignore[type-arg]

CONFIG_KEY = "mg:v1:config"
BLOCK_THRESHOLD_FIELD = "block_threshold"
DEFAULT_CACHE_SECONDS = 5.0


class RedisRuntimeConfig:
    """The live block-threshold override, or None when there is no override."""

    def __init__(
        self,
        redis_client: RedisClient,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
    ):
        self._r = redis_client
        self._cache_seconds = cache_seconds
        self._cached: float | None = None
        self._cached_at = 0.0

    def block_threshold(self) -> float | None:
        """The override, or None to use whatever the process was started with.

        Every failure path returns None. A config store that is down, or holds
        something unparseable, must leave the configured threshold standing —
        never silently block everyone or stop blocking anyone.
        """
        now = time.monotonic()
        if self._cached_at and now - self._cached_at < self._cache_seconds:
            return self._cached

        try:
            # Through a pipeline so redis-py's sync/async union type resolves,
            # the same way redis_events reads its ring.
            pipe = self._r.pipeline()
            pipe.hget(CONFIG_KEY, BLOCK_THRESHOLD_FIELD)
            (raw,) = pipe.execute()
        except redis.RedisError:
            logger.warning("runtime config unreachable, using configured threshold")
            return None

        value: float | None
        if raw is None:
            value = None
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "runtime config holds an unreadable block_threshold (%r), ignoring",
                    raw,
                )
                value = None

        self._cached = value
        self._cached_at = now
        return value

    def set_block_threshold(self, value: float | None) -> None:
        """Set the override, or clear it with None."""
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"block_threshold must be between 0 and 1, got {value}")

        if value is None:
            self._r.hdel(CONFIG_KEY, BLOCK_THRESHOLD_FIELD)
        else:
            self._r.hset(CONFIG_KEY, BLOCK_THRESHOLD_FIELD, str(value))
        self._cached = value
        self._cached_at = time.monotonic()
