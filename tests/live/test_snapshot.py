"""Tests for SessionSnapshot — what a store hands back to the scorer.

`record_request` used to return a bare LiveSession. It now also carries the
signals resolved for that actor, because reading them separately would mean a
second Redis round trip on the one code path that runs for every visitor
request behind nginx `auth_request`.

The parity test in TestStoreShapeParity is the important one here: the suite
scores against an in-memory double, so a double that returns a different shape
than production would make every live test pass against something Redis never
produces.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
import redis

from microguard.live.redis_store import RedisSessionStateStore
from microguard.live.state import SessionSnapshot, SessionStateStore
from microguard.parser import LogEntry
from microguard.signals import EMPTY_SIGNALS

BASE = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)


def _entry(ip="203.0.113.5", url="/api/items", offset=0):
    return LogEntry(
        ip=ip,
        timestamp=BASE + timedelta(seconds=offset),
        method="GET",
        url=url,
        status=200,
        size=100,
        referer="",
        user_agent="Mozilla/5.0",
    )


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
def redis_store(redis_client):
    redis_client.flushdb()
    return RedisSessionStateStore(redis_client, default_ttl=10)


class TestSnapshotShape:
    def test_the_double_returns_a_snapshot(self, store):
        snapshot = store.record_request("203.0.113.5", "Mozilla/5.0", _entry())
        assert isinstance(snapshot, SessionSnapshot)

    def test_the_snapshot_carries_the_post_append_session(self, store):
        store.record_request("203.0.113.5", "Mozilla/5.0", _entry(url="/a"))
        snapshot = store.record_request("203.0.113.5", "Mozilla/5.0", _entry(url="/b", offset=5))
        assert snapshot.session.request_count == 2
        assert [e.url for e in snapshot.session.requests] == ["/a", "/b"]

    def test_no_signal_data_means_unresolved_not_empty(self, store):
        """The batch trap again, at the storage layer. An actor with nothing
        recorded must read as 'not looked up', never as 'looked up, clean'."""
        snapshot = store.record_request("203.0.113.5", "Mozilla/5.0", _entry())
        assert snapshot.signals == EMPTY_SIGNALS
        assert snapshot.signals.resolved is False


class TestStoreShapeParity:
    """CRITICAL REGRESSION. The suite scores against the in-memory double."""

    def test_both_stores_return_the_same_snapshot_fields(self, store, redis_store):
        from_double = store.record_request("203.0.113.5", "Mozilla/5.0", _entry())
        from_redis = redis_store.record_request("203.0.113.5", "Mozilla/5.0", _entry())

        assert type(from_double) is type(from_redis)
        assert vars(from_double).keys() == vars(from_redis).keys()
        assert vars(from_double.signals) == vars(from_redis.signals)

    def test_both_stores_still_satisfy_the_protocol(self, store, redis_store):
        assert isinstance(store, SessionStateStore)
        assert isinstance(redis_store, SessionStateStore)

    def test_both_stores_agree_on_a_replayed_sequence(self, store, redis_store):
        for i in range(4):
            double = store.record_request("203.0.113.5", "Mozilla/5.0", _entry(offset=i))
            real = redis_store.record_request("203.0.113.5", "Mozilla/5.0", _entry(offset=i))
            assert double.session.request_count == real.session.request_count
            assert double.session.duration == real.session.duration


class TestSignalsComeFromRedis:
    def test_a_recorded_signal_is_resolved(self, redis_client, redis_store):
        redis_client.set(
            "mg:v1:signals:203.0.113.5",
            json.dumps({"tor_exit": True, "abuse_score": 91.0}),
        )
        snapshot = redis_store.record_request("203.0.113.5", "Mozilla/5.0", _entry())
        assert snapshot.signals.resolved is True
        assert snapshot.signals.tor_exit is True
        assert snapshot.signals.abuse_score == 91.0

    def test_unknown_fields_are_ignored_rather_than_raising(self, redis_client, redis_store):
        """A newer refresher writing a field this reader does not know about
        must not take the request path down."""
        redis_client.set(
            "mg:v1:signals:203.0.113.5",
            json.dumps({"tor_exit": True, "some_future_source": 1}),
        )
        snapshot = redis_store.record_request("203.0.113.5", "Mozilla/5.0", _entry())
        assert snapshot.signals.tor_exit is True

    def test_a_corrupt_value_reads_as_unresolved(self, redis_client, redis_store):
        redis_client.set("mg:v1:signals:203.0.113.5", "{not json")
        snapshot = redis_store.record_request("203.0.113.5", "Mozilla/5.0", _entry())
        assert snapshot.signals.resolved is False

    def test_the_signals_read_costs_no_extra_round_trip(self, redis_client):
        """Decision 2A's whole reason for changing this interface."""
        executions = []
        real_pipeline = redis_client.pipeline

        def counting_pipeline(*args, **kwargs):
            pipe = real_pipeline(*args, **kwargs)
            real_execute = pipe.execute

            def execute(*a, **k):
                executions.append(1)
                return real_execute(*a, **k)

            pipe.execute = execute
            return pipe

        redis_client.flushdb()
        redis_client.pipeline = counting_pipeline
        try:
            store = RedisSessionStateStore(redis_client, default_ttl=10)
            store.record_request("203.0.113.5", "Mozilla/5.0", _entry())
        finally:
            redis_client.pipeline = real_pipeline

        assert executions == [1], f"expected one pipeline execution, got {len(executions)}"
