"""Tests for the dashboard's live endpoints.

These run against an in-process recorder — no Redis — because that is also how
the app behaves when Redis is absent, and it keeps the HTTP contract honest
independent of the backend.
"""

import asyncio
import json

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient


def decision(ip="10.0.0.1", label="human", score=0.1):
    return {
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


def _fake_request(app):
    """Minimal Starlette Request carrying the app, for calling a handler directly."""
    from starlette.requests import Request

    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app})


def _drain(stream, after_first=None):
    """Collect an async generator's events from a sync test.

    The repo has no asyncio pytest plugin configured, so async generators are
    driven through asyncio.run() the same way tests/live/test_middleware.py
    drives the ASGI middleware.
    """

    async def collect():
        events = []
        async for event in stream:
            events.append(event)
            if after_first is not None and len(events) == 1:
                after_first()
        return events

    return asyncio.run(collect())


@pytest.fixture
def recorder():
    from microguard.events import InMemoryDecisionRecorder
    return InMemoryDecisionRecorder()


@pytest.fixture
def client(recorder):
    from microguard.dashboard.app import create_app
    return TestClient(create_app(recorder=recorder))


class TestLiveStats:
    def test_reports_the_recorder_counters(self, client, recorder):
        recorder.record(decision(label="bot", score=0.9))
        recorder.record(decision(label="human"))

        body = client.get("/api/live/stats").json()

        assert body["total"] == 2
        assert body["blocked"] == 1
        assert body["bot_rate"] == 0.5
        assert len(body["histogram"]) == 20

    def test_is_zeroed_not_absent_before_any_traffic(self, client):
        body = client.get("/api/live/stats").json()

        assert body["total"] == 0
        assert body["top_blocked_ips"] == []


class TestLiveEvents:
    def test_returns_decisions_newest_first(self, client, recorder):
        recorder.record(decision(ip="first"))
        recorder.record(decision(ip="second"))

        events = client.get("/api/live/events").json()["events"]

        assert [e["ip"] for e in events] == ["second", "first"]

    def test_limit_is_honored(self, client, recorder):
        for name in ("a", "b", "c"):
            recorder.record(decision(ip=name))

        events = client.get("/api/live/events?limit=2").json()["events"]

        assert len(events) == 2

    def test_limit_is_bounded(self, client):
        response = client.get("/api/live/events?limit=100000")

        assert response.status_code == 422


class TestLiveStream:
    """The SSE generator is tested directly.

    Driving it over HTTP would mean reading from a stream that never ends;
    `_max_ticks` terminates it the same way watch_logfile's `_max_iterations`
    does, and is likewise test-only.
    """

    def test_opens_with_the_current_stats(self, recorder):
        from microguard.dashboard.api_live import decision_stream

        recorder.record(decision(label="bot", score=0.9))

        events = _drain(decision_stream(recorder, _max_ticks=1))

        assert events[0]["event"] == "stats"
        assert json.loads(events[0]["data"])["blocked"] == 1

    def test_replays_the_backlog_then_streams_new_decisions(self, recorder):
        from microguard.dashboard.api_live import decision_stream

        recorder.record(decision(ip="10.0.0.7"))

        events = _drain(decision_stream(recorder, _max_ticks=1))

        decisions = [json.loads(e["data"]) for e in events if e["event"] == "decision"]
        assert [d["ip"] for d in decisions] == ["10.0.0.7"]

    def test_does_not_resend_decisions_it_already_sent(self, recorder):
        from microguard.dashboard.api_live import decision_stream

        recorder.record(decision(ip="10.0.0.7"))

        events = _drain(decision_stream(recorder, poll_interval=0.0, _max_ticks=2))

        decisions = [json.loads(e["data"]) for e in events if e["event"] == "decision"]
        assert len(decisions) == 1

    def test_sends_decisions_recorded_between_ticks(self, recorder):
        from microguard.dashboard.api_live import decision_stream

        stream = decision_stream(recorder, poll_interval=0.0, _max_ticks=2)
        sent = _drain(stream, after_first=lambda: recorder.record(decision(ip="10.0.0.8")))

        decisions = [json.loads(e["data"]) for e in sent if e["event"] == "decision"]
        assert [d["ip"] for d in decisions] == ["10.0.0.8"]


class TestLiveStreamEndpoint:
    """Checked without consuming the stream — it never ends by design."""

    def test_is_registered_as_an_event_stream(self, recorder):
        from microguard.dashboard.api_live import stream
        from microguard.dashboard.app import create_app

        app = create_app(recorder=recorder)
        request = _fake_request(app)

        response = asyncio.run(stream(request))

        assert response.media_type == "text/event-stream"


class TestRecorderSelection:
    """How the app decides where live decisions come from."""

    def test_without_a_redis_url_it_records_in_process(self):
        from microguard.dashboard.app import create_app
        from microguard.events import InMemoryDecisionRecorder

        app = create_app()

        assert isinstance(app.state.recorder, InMemoryDecisionRecorder)
        assert TestClient(app).get("/api/health").json()["redis_connected"] is False

    def test_an_unreachable_redis_degrades_instead_of_failing_to_start(self):
        from microguard.dashboard.app import create_app
        from microguard.events import InMemoryDecisionRecorder

        app = create_app(redis_url="redis://127.0.0.1:6390")

        assert isinstance(app.state.recorder, InMemoryDecisionRecorder)
        assert TestClient(app).get("/api/health").json()["redis_connected"] is False

    def test_a_reachable_redis_backs_the_live_feed(self):
        redis = pytest.importorskip("redis")
        from microguard.dashboard.app import create_app
        from microguard.live.redis_events import RedisDecisionRecorder

        try:
            redis.Redis(host="localhost", port=6379, db=15).ping()
        except redis.ConnectionError:
            pytest.skip("Redis not available on localhost:6379")

        app = create_app(redis_url="redis://localhost:6379/15")

        assert isinstance(app.state.recorder, RedisDecisionRecorder)
        assert TestClient(app).get("/api/health").json()["redis_connected"] is True


class TestLiveConfig:
    """The block threshold is readable always, writable only on request."""

    @pytest.fixture
    def redis_client(self):
        redis = pytest.importorskip("redis")
        try:
            client = redis.Redis.from_url("redis://localhost:6379/15", decode_responses=True)
            client.ping()
        except redis.ConnectionError:
            pytest.skip("Redis not available on localhost:6379")
        client.flushdb()
        yield client
        client.flushdb()

    def test_reports_no_override_when_none_is_set(self, client):
        body = client.get("/api/live/config").json()

        assert body["block_threshold"] is None
        assert body["writable"] is False

    def test_writes_are_refused_unless_explicitly_enabled(self, client):
        response = client.put("/api/live/config", json={"block_threshold": 0.5})

        assert response.status_code == 403

    def test_writes_need_a_shared_config_store(self):
        from microguard.dashboard.app import create_app

        writable = TestClient(create_app(allow_config_writes=True))

        response = writable.put("/api/live/config", json={"block_threshold": 0.5})

        assert response.status_code == 503

    def test_an_enabled_write_reaches_the_shared_config(self, redis_client):
        from microguard.dashboard.app import create_app
        from microguard.live.runtime_config import RedisRuntimeConfig

        app_client = TestClient(
            create_app(redis_url="redis://localhost:6379/15", allow_config_writes=True)
        )

        response = app_client.put("/api/live/config", json={"block_threshold": 0.25})

        assert response.status_code == 200
        assert RedisRuntimeConfig(redis_client, cache_seconds=0.0).block_threshold() == 0.25

    def test_the_override_can_be_cleared(self, redis_client):
        from microguard.dashboard.app import create_app
        from microguard.live.runtime_config import RedisRuntimeConfig

        app_client = TestClient(
            create_app(redis_url="redis://localhost:6379/15", allow_config_writes=True)
        )
        app_client.put("/api/live/config", json={"block_threshold": 0.25})

        app_client.put("/api/live/config", json={"block_threshold": None})

        assert RedisRuntimeConfig(redis_client, cache_seconds=0.0).block_threshold() is None

    def test_an_out_of_range_threshold_is_rejected(self, redis_client):
        from microguard.dashboard.app import create_app

        app_client = TestClient(
            create_app(redis_url="redis://localhost:6379/15", allow_config_writes=True)
        )

        response = app_client.put("/api/live/config", json={"block_threshold": 1.5})

        assert response.status_code == 422
