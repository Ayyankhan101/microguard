"""Tests for the decision id and the feature vector carried on each decision.

Neither existed. `mg:v1:events` held a JSON blob in a LIST with no identifier,
so an operator clicking "this was wrong" on a dashboard row had nothing to
reference, and the features the decision was actually made from were never
stored at all -- the live session is a 200-entry sliding window on a 1800s TTL,
so by the time anyone reviews a block, the inputs are gone.
"""

import json
from datetime import datetime, timezone

import pytest
import redis

from microguard.events import InMemoryDecisionRecorder
from microguard.live.redis_events import RedisDecisionRecorder
from microguard.live.scorer import LiveScorer
from microguard.parser import LogEntry


def _entry(ip="203.0.113.5", url="/products"):
    return LogEntry(
        ip=ip, timestamp=datetime.now(timezone.utc), method="GET", url=url,
        status=200, size=1, referer="", user_agent="Mozilla/5.0",
    )


@pytest.fixture(scope="module")
def redis_client():
    try:
        c = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        c.ping()
        yield c
        c.flushdb()
        c.close()
    except redis.ConnectionError:
        pytest.skip("Redis not available on localhost:6379")


@pytest.fixture()
def clean(redis_client):
    redis_client.flushdb()
    return redis_client


class TestDecisionId:
    def test_every_recorded_decision_has_an_id(self, store, clean):
        scorer = LiveScorer(store, recorder=RedisDecisionRecorder(clean))
        scorer.score_request(_entry())

        event = json.loads(clean.lrange("mg:v1:events", 0, 0)[0])
        assert event["id"]

    def test_ids_are_unique_across_decisions(self, store, clean):
        scorer = LiveScorer(store, recorder=RedisDecisionRecorder(clean))
        for i in range(20):
            scorer.score_request(_entry(url=f"/p/{i}"))

        ids = [json.loads(e)["id"] for e in clean.lrange("mg:v1:events", 0, -1)]
        assert len(set(ids)) == 20

    def test_the_in_memory_recorder_agrees(self, store):
        """The dashboard runs against either recorder, so a row from one must
        be addressable the same way as a row from the other."""
        scorer = LiveScorer(store, recorder=InMemoryDecisionRecorder())
        scorer.score_request(_entry())

        assert scorer._recorder.recent(1)[0]["id"]

    def test_an_id_survives_the_round_trip_through_recent(self, store, clean):
        recorder = RedisDecisionRecorder(clean)
        LiveScorer(store, recorder=recorder).score_request(_entry())

        recent = recorder.recent(1)[0]
        raw = json.loads(clean.lrange("mg:v1:events", 0, 0)[0])
        assert recent["id"] == raw["id"]


class TestFeatureCapture:
    def test_the_features_the_decision_used_are_stored(self, store, clean):
        scorer = LiveScorer(store, recorder=RedisDecisionRecorder(clean))
        scorer.score_request(_entry())

        event = json.loads(clean.lrange("mg:v1:events", 0, 0)[0])
        assert len(event["features"]) == 19

    def test_features_are_captured_even_with_no_model(self, store, clean):
        """Feedback has to work in heuristics-only mode. Without this the
        correction would have no vector to train on precisely when the model
        most needs replacing."""
        scorer = LiveScorer(store, model_path="/nonexistent", recorder=RedisDecisionRecorder(clean))
        assert scorer.model_loaded is False

        scorer.score_request(_entry())
        event = json.loads(clean.lrange("mg:v1:events", 0, 0)[0])
        assert len(event["features"]) == 19

    def test_the_short_circuit_path_carries_features_too(self, store, clean):
        """An automated-integration verdict is still a verdict an operator can
        disagree with."""
        scorer = LiveScorer(store, recorder=RedisDecisionRecorder(clean))
        entry = _entry()
        entry.user_agent = "Slackbot 1.0 (+https://api.slack.com/robots)"
        scorer.score_request(entry)

        event = json.loads(clean.lrange("mg:v1:events", 0, 0)[0])
        assert len(event["features"]) == 19

    def test_nothing_is_computed_when_no_consumer_exists(self, store):
        """No model and no recorder means nothing reads the vector, and
        extract_features is the dominant per-request cost."""
        scorer = LiveScorer(store, model_path="/nonexistent", recorder=None)
        result = scorer.score_request(_entry())
        assert "features" not in result or result["features"] is None
