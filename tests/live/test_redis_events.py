"""Tests for RedisDecisionRecorder — live Redis only, no mocks.

The in-memory recorder's semantics are pinned in tests/test_events.py; these
cover what only a real server can show: shared state across processes, the ring
cap, and that recording is one round trip.
"""

import pytest
import redis

from microguard.events import DecisionRecorder
from microguard.live.redis_events import RedisDecisionRecorder


@pytest.fixture(scope="module")
def redis_client():
    try:
        client = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        client.ping()
        yield client
        client.flushdb()
        client.close()
    except redis.ConnectionError:
        pytest.skip("Redis not available on localhost:6379")


@pytest.fixture()
def recorder(redis_client):
    redis_client.flushdb()
    return RedisDecisionRecorder(redis_client)


def decision(ip="10.0.0.1", label="human", score=0.1, **overrides):
    payload = {
        "ip": ip,
        "label": label,
        "score": score,
        "model_score": 0.0,
        "heuristic_label": label,
        "heuristic_confidence": 0.5,
        "heuristic_reason": "test",
        "request_count": 1,
        "duration": 0.0,
        "model_loaded": True,
        "reason": "test",
    }
    payload.update(overrides)
    return payload


def test_satisfies_the_recorder_protocol(recorder):
    assert isinstance(recorder, DecisionRecorder)


def test_counts_total_blocked_and_allowed(recorder):
    recorder.record(decision(label="bot"))
    recorder.record(decision(label="human"))

    stats = recorder.stats()
    assert stats["total"] == 2
    assert stats["blocked"] == 1
    assert stats["allowed"] == 1


def test_state_is_shared_between_recorder_instances(recorder, redis_client):
    recorder.record(decision(label="bot"))

    other = RedisDecisionRecorder(redis_client)

    assert other.stats()["blocked"] == 1


def test_returns_decisions_newest_first(recorder):
    recorder.record(decision(ip="first"))
    recorder.record(decision(ip="second"))

    assert [d["ip"] for d in recorder.recent()] == ["second", "first"]


def test_round_trips_the_full_decision_payload(recorder):
    recorder.record(decision(heuristic_reason="known bot UA", score=0.91, label="bot"))

    event = recorder.recent()[0]
    assert event["heuristic_reason"] == "known bot UA"
    assert event["score"] == 0.91
    assert isinstance(event["ts"], float)


def test_ring_is_capped(recorder, redis_client):
    capped = RedisDecisionRecorder(redis_client, capacity=2)

    for name in ("a", "b", "c"):
        capped.record(decision(ip=name))

    assert [d["ip"] for d in capped.recent()] == ["c", "b"]


def test_histogram_buckets_scores(recorder):
    recorder.record(decision(score=0.0))
    recorder.record(decision(score=1.0))

    histogram = recorder.stats()["histogram"]
    assert len(histogram) == 20
    assert histogram[0] == 1
    assert histogram[19] == 1


def test_ranks_blocked_ips(recorder):
    recorder.record(decision(ip="10.0.0.2", label="bot"))
    recorder.record(decision(ip="10.0.0.2", label="bot"))
    recorder.record(decision(ip="10.0.0.1", label="bot"))

    assert recorder.stats()["top_blocked_ips"][0] == {"ip": "10.0.0.2", "count": 2}


def test_avg_score_is_the_mean_of_everything_recorded(recorder):
    recorder.record(decision(score=0.2))
    recorder.record(decision(score=0.4))

    assert recorder.stats()["avg_score"] == pytest.approx(0.3)


def test_empty_store_reports_zeroes(recorder):
    stats = recorder.stats()

    assert stats["total"] == 0
    assert stats["bot_rate"] == 0.0
    assert stats["avg_score"] == 0.0
    assert stats["histogram"] == [0] * 20


def test_recording_is_one_round_trip(recorder, redis_client):
    calls = []
    real_execute = redis.client.Pipeline.execute

    def counting_execute(self, *args, **kwargs):
        calls.append(1)
        return real_execute(self, *args, **kwargs)

    redis.client.Pipeline.execute = counting_execute
    try:
        recorder.record(decision(label="bot"))
    finally:
        redis.client.Pipeline.execute = real_execute

    assert len(calls) == 1


def test_an_unreadable_event_is_skipped_rather_than_blanking_the_feed(recorder, redis_client):
    recorder.record(decision(ip="10.0.0.5"))
    redis_client.lpush("mg:v1:events", "this is not json")

    events = recorder.recent()

    assert [d["ip"] for d in events] == ["10.0.0.5"]
