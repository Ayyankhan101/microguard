"""Tests for RedisSessionStateStore — live Redis only, no mocks."""

import time
from datetime import datetime, timezone

import pytest
import redis

from microguard.live.redis_store import RedisSessionStateStore
from microguard.live.state import LiveSession
from microguard.parser import LogEntry


@pytest.fixture(scope="module")
def redis_client():
    """Real Redis connection — skip if Redis unavailable."""
    try:
        client = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        client.ping()
        yield client
        client.flushdb()
        client.close()
    except redis.ConnectionError:
        pytest.skip("Redis not available on localhost:6379")


@pytest.fixture()
def store(redis_client):
    """Fresh store with per-test cleanup."""
    redis_client.flushdb()
    return RedisSessionStateStore(redis_client, default_ttl=10)


def _make_entry(ip="1.2.3.4", status=200, url="/api/test", **kwargs):
    defaults = {
        "ip": ip,
        "timestamp": datetime.now(timezone.utc),
        "method": "GET",
        "url": url,
        "status": status,
        "size": 100,
        "referer": "",
        "user_agent": "Mozilla/5.0",
    }
    defaults.update(kwargs)
    return LogEntry(**defaults)


def _make_session(ip="1.2.3.4", n_requests=3):
    session = LiveSession(ip=ip, user_agent="Mozilla/5.0")
    for i in range(n_requests):
        entry = _make_entry(ip=ip, url=f"/api/endpoint/{i}")
        session.add_request(entry)
    return session


# --- Basic CRUD ---


def test_get_returns_none_for_missing(store):
    assert store.get("live:1.2.3.4") is None


def test_set_then_get_returns_session(store):
    session = _make_session()
    store.set("live:1.2.3.4", session)
    got = store.get("live:1.2.3.4")
    assert got is not None
    assert got.ip == "1.2.3.4"
    assert got.request_count == 3
    assert got.user_agent == "Mozilla/5.0"


def test_session_requests_preserved(store):
    session = _make_session(n_requests=5)
    store.set("live:1.2.3.4", session)
    got = store.get("live:1.2.3.4")
    assert got is not None
    assert len(got.requests) == 5
    for entry in got.requests:
        assert entry.ip == "1.2.3.4"
        assert entry.status == 200


def test_delete_removes_session(store):
    session = _make_session()
    store.set("live:1.2.3.4", session)
    store.delete("live:1.2.3.4")
    assert store.get("live:1.2.3.4") is None


def test_delete_removes_score_too(store):
    session = _make_session()
    store.set("live:1.2.3.4", session)
    store.incr_score("live:1.2.3.4", 5.0)
    assert store.get_score("live:1.2.3.4") == 5.0
    store.delete("live:1.2.3.4")
    assert store.get_score("live:1.2.3.4") == 0.0


# --- TTL expiry ---


def test_session_expires(store, redis_client):
    session = _make_session()
    store.set("live:1.2.3.4", session, ttl_seconds=1)
    assert store.get("live:1.2.3.4") is not None
    time.sleep(1.1)
    assert store.get("live:1.2.3.4") is None


def test_score_expires(store, redis_client):
    session = _make_session()
    store.set("live:1.2.3.4", session, ttl_seconds=1)
    store.incr_score("live:1.2.3.4", 3.0)
    assert store.get_score("live:1.2.3.4") == 3.0
    time.sleep(1.1)
    assert store.get_score("live:1.2.3.4") == 0.0


# --- Score operations ---


def test_incr_score_returns_new_value(store):
    session = _make_session()
    store.set("live:1.2.3.4", session)
    v1 = store.incr_score("live:1.2.3.4", 1.0)
    v2 = store.incr_score("live:1.2.3.4", 2.5)
    assert v1 == 1.0
    assert v2 == 3.5


def test_get_score_returns_zero_for_missing(store):
    assert store.get_score("live:nonexistent") == 0.0


def test_incr_score_on_new_key(store):
    val = store.incr_score("live:5.6.7.8", 1.0)
    assert val == 1.0


# --- Multiple sessions ---


def test_multiple_sessions_isolated(store):
    s1 = _make_session(ip="1.1.1.1")
    s2 = _make_session(ip="2.2.2.2")
    store.set("live:1.1.1.1", s1)
    store.set("live:2.2.2.2", s2)
    store.incr_score("live:1.1.1.1", 5.0)

    assert store.get_score("live:1.1.1.1") == 5.0
    assert store.get_score("live:2.2.2.2") == 0.0
    assert store.get("live:1.1.1.1") is not None
    assert store.get("live:2.2.2.2") is not None


def test_keys_returns_matching_sessions(store):
    store.set("live:1.1.1.1", _make_session(ip="1.1.1.1"))
    store.set("live:2.2.2.2", _make_session(ip="2.2.2.2"))
    keys = store.keys("live:*")
    assert "live:1.1.1.1" in keys
    assert "live:2.2.2.2" in keys


# --- Protocol conformance ---


def test_store_implements_protocol():
    from microguard.live.state import SessionStateStore
    assert isinstance(
        RedisSessionStateStore(redis.Redis()), SessionStateStore
    )
