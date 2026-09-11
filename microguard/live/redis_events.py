"""Redis-backed decision recording for the live dashboard.

Requires `pip install microguard[live]`. The Protocol and the process-local
implementation live in microguard/events.py, which imports without Redis.

    Key schema (prefix mg:v1:)

        mg:v1:events       LIST of JSON decisions, newest FIRST, capped
        mg:v1:counters     HASH  total / blocked / score_sum
        mg:v1:hist         HASH  bucket index 0-19 -> count
        mg:v1:blocked_ips  ZSET  ip -> times blocked, capped

    One decision == one pipeline, one round trip:

        LPUSH   mg:v1:events       <decision json>
        LTRIM   mg:v1:events       0 <capacity-1>
        HINCRBY mg:v1:counters     total 1
        HINCRBY mg:v1:counters     blocked 1          (blocked decisions only)
        HINCRBYFLOAT mg:v1:counters score_sum <score>
        HINCRBY mg:v1:hist         <bucket> 1
        ZINCRBY mg:v1:blocked_ips  1 <ip>             (blocked decisions only)
        ZREMRANGEBYRANK mg:v1:blocked_ips 0 -N-1      (blocked decisions only)

Why counters rather than aggregating the ring: the ring is capped, so totals
derived from it would silently shrink as history scrolls off. An operator
watching "blocked today" drop while nothing improved is worse than no number.

Why newest-first (LPUSH) here but newest-last (RPUSH) in redis_store: that
store re-reads the whole list in order to rebuild a session, while this one
only ever reads the newest N. LPUSH + LRANGE 0 N-1 is that read directly.

Why the blocked-IP set is trimmed: it is the only structure here that would
grow with the number of DISTINCT attackers rather than with traffic volume, so
against a rotating botnet it had no ceiling. ZREMRANGEBYRANK drops the lowest
ranks, which are the lowest counts, keeping the busiest — see MAX_TRACKED_IPS
in events.py for what that costs.

Keys are unversioned by IP and never expire on their own — they are process
lifetime counters for an operator, not per-visitor state. Bump the prefix
version whenever the value shape changes, for the same WRONGTYPE reason
redis_store documents.
"""

from __future__ import annotations

import json
import time

import redis

from ..events import (
    DEFAULT_CAPACITY,
    MAX_TRACKED_IPS,
    SCORE_BUCKETS,
    TOP_IPS,
    score_bucket,
)

RedisClient = redis.Redis  # type: ignore[type-arg]


class RedisDecisionRecorder:
    """Decision recorder backed by Redis, shared across processes.

    Thread-safe by way of redis-py: each thread takes its own connection from
    the pool and a fresh pipeline per call.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        prefix: str = "mg:v1:",
        capacity: int = DEFAULT_CAPACITY,
    ):
        self._r = redis_client
        self._prefix = prefix
        self._capacity = capacity

    @property
    def _events_key(self) -> str:
        return f"{self._prefix}events"

    @property
    def _counters_key(self) -> str:
        return f"{self._prefix}counters"

    @property
    def _hist_key(self) -> str:
        return f"{self._prefix}hist"

    @property
    def _blocked_ips_key(self) -> str:
        return f"{self._prefix}blocked_ips"

    def record(self, result: dict) -> None:
        """Store one decision. One pipeline, one round trip."""
        event = dict(result)
        event["ts"] = time.time()
        score = float(result.get("score", 0.0))
        blocked = result.get("label") == "bot"

        pipe = self._r.pipeline()
        pipe.lpush(self._events_key, json.dumps(event))
        pipe.ltrim(self._events_key, 0, self._capacity - 1)
        pipe.hincrby(self._counters_key, "total", 1)
        pipe.hincrbyfloat(self._counters_key, "score_sum", score)
        pipe.hincrby(self._hist_key, str(score_bucket(score)), 1)
        if blocked:
            pipe.hincrby(self._counters_key, "blocked", 1)
            pipe.zincrby(self._blocked_ips_key, 1, result.get("ip", ""))
            # Ranks ascend by score, so this drops the lowest counts and keeps
            # the busiest. A no-op until the set is actually full, and it rides
            # the pipeline that was already being sent — still one round trip.
            pipe.zremrangebyrank(self._blocked_ips_key, 0, -MAX_TRACKED_IPS - 1)
        pipe.execute()

    def stats(self) -> dict:
        """Aggregate counters. One pipeline, one round trip."""
        pipe = self._r.pipeline()
        pipe.hgetall(self._counters_key)
        pipe.hgetall(self._hist_key)
        pipe.zrevrange(self._blocked_ips_key, 0, TOP_IPS - 1, withscores=True)
        counters, raw_histogram, raw_ips = pipe.execute()

        total = int(counters.get("total", 0))
        blocked = int(counters.get("blocked", 0))
        score_sum = float(counters.get("score_sum", 0.0))

        histogram = [0] * SCORE_BUCKETS
        for bucket, count in raw_histogram.items():
            index = int(bucket)
            if 0 <= index < SCORE_BUCKETS:
                histogram[index] = int(count)

        return {
            "total": total,
            "blocked": blocked,
            "allowed": total - blocked,
            "bot_rate": blocked / total if total else 0.0,
            "avg_score": score_sum / total if total else 0.0,
            "histogram": histogram,
            "top_blocked_ips": [
                {"ip": ip, "count": int(count)} for ip, count in raw_ips
            ],
            "started_at": None,
        }

    def recent(self, limit: int = 100) -> list[dict]:
        """The most recent decisions, newest first."""
        # Through a pipeline for the same reason as everything else here: one
        # round trip, and redis-py's sync/async union types resolve.
        pipe = self._r.pipeline()
        pipe.lrange(self._events_key, 0, limit - 1)
        (raw_events,) = pipe.execute()
        events = []
        for raw in raw_events:
            try:
                events.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                # One unreadable event must not blank the whole feed.
                continue
        return events
