"""Decision recording — what the live dashboard reads.

LiveScorer computes a full decision per request and returns it to the caller,
who acts on the verdict and drops the rest. Nothing in the project retained a
history of those decisions, so no operator could answer "what did microguard
block, and why?" after the fact. A recorder is the seam that keeps them.

This module is deliberately NOT under live/: live/__init__.py raises
ImportError when the 'live' extra is absent, and the dashboard has to run
without Redis. The Redis-backed recorder lives in live/redis_events.py.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Protocol, runtime_checkable

# Twenty buckets of 0.05 — the same resolution the dashboard's score histogram
# and the model tab's distribution use.
SCORE_BUCKETS = 20
DEFAULT_CAPACITY = 1000
TOP_IPS = 10


def score_bucket(score: float) -> int:
    """Bucket index for a 0..1 score. 1.0 clamps into the last bucket."""
    return min(int(score * SCORE_BUCKETS), SCORE_BUCKETS - 1)


@runtime_checkable
class DecisionRecorder(Protocol):
    """Somewhere to put decisions so a dashboard can read them back.

    Implementations must be thread-safe: the check server is threaded
    (live/server.py:186) and records from every request thread.
    """

    def record(self, result: dict) -> None:
        """Store one decision. Must never raise into the caller's hot path."""
        ...

    def stats(self) -> dict:
        """Aggregate counters over everything recorded since start."""
        ...

    def recent(self, limit: int = 100) -> list[dict]:
        """The most recent decisions, newest first."""
        ...


class InMemoryDecisionRecorder:
    """Process-local recorder — one dashboard, no Redis.

    Counters are cumulative totals rather than derived from the ring, so the
    numbers don't quietly change as old decisions scroll out of it.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=capacity)
        self._blocked_ips: Counter[str] = Counter()
        self._histogram = [0] * SCORE_BUCKETS
        self._total = 0
        self._blocked = 0
        self._score_sum = 0.0
        self._started_at = time.time()

    def record(self, result: dict) -> None:
        event = dict(result)
        event["ts"] = time.time()
        score = float(result.get("score", 0.0))
        with self._lock:
            self._events.append(event)
            self._total += 1
            self._score_sum += score
            self._histogram[score_bucket(score)] += 1
            if result.get("label") == "bot":
                self._blocked += 1
                self._blocked_ips[result.get("ip", "")] += 1

    def stats(self) -> dict:
        with self._lock:
            total = self._total
            return {
                "total": total,
                "blocked": self._blocked,
                "allowed": total - self._blocked,
                "bot_rate": self._blocked / total if total else 0.0,
                "avg_score": self._score_sum / total if total else 0.0,
                "histogram": list(self._histogram),
                "top_blocked_ips": [
                    {"ip": ip, "count": count}
                    for ip, count in self._blocked_ips.most_common(TOP_IPS)
                ],
                "started_at": self._started_at,
            }

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            events = list(self._events)
        return [dict(event) for event in reversed(events[-limit:])]
