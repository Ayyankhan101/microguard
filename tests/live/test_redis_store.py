"""Tests for RedisSessionStateStore — live Redis only, no mocks."""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import redis

from microguard.live.redis_store import (
    MAX_SESSION_ENTRIES,
    RedisSessionStateStore,
    _session_from,
)
from microguard.live.state import SessionStateStore
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


def _record(store, n, ip="1.2.3.4", **kwargs):
    """Record n requests and return the session from the last call."""
    session = None
    for i in range(n):
        session = store.record_request(
            ip, "Mozilla/5.0", _make_entry(ip=ip, url=f"/api/endpoint/{i}", **kwargs)
        )
    return session


# --- Appending ---


def test_first_request_creates_the_session(store):
    session = store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry())
    assert session.ip == "1.2.3.4"
    assert session.user_agent == "Mozilla/5.0"
    assert session.request_count == 1


def test_appends_accumulate(store):
    assert _record(store, 5).request_count == 5


def test_appends_preserve_order(store):
    _record(store, 4)
    session = store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry(url="/last"))
    assert [e.url for e in session.requests] == [
        "/api/endpoint/0",
        "/api/endpoint/1",
        "/api/endpoint/2",
        "/api/endpoint/3",
        "/last",
    ]


def test_entry_fields_survive_the_round_trip(store):
    entry = _make_entry(url="/api/x", status=404, raw_line="GET /api/x HTTP/1.0")
    session = store.record_request("1.2.3.4", "Mozilla/5.0", entry)
    got = session.requests[0]
    assert got.url == "/api/x"
    assert got.status == 404
    # raw_line feeds labeler.py's HTTP/1.0 rule — losing it disables that rule.
    assert got.raw_line == "GET /api/x HTTP/1.0"


def test_sessions_are_isolated_by_ip(store):
    _record(store, 3, ip="1.1.1.1")
    session = _record(store, 1, ip="2.2.2.2")
    assert session.request_count == 1


def test_delete_removes_the_session(store):
    _record(store, 3)
    store.delete("1.2.3.4")
    assert store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry()).request_count == 1


# --- Concurrency: the reason this store is a LIST and not a JSON blob ---


def test_concurrent_appends_do_not_lose_requests(store):
    """Every concurrent append from one actor must survive.

    The previous get/mutate/set store failed this badly: 20 threads, 9 requests
    survived. Undercounting request_count blunts the rate and timing rules
    under exactly the load they exist to catch.
    """
    workers = 20
    start = threading.Barrier(workers)
    errors: list[BaseException] = []

    def append_one() -> None:
        try:
            start.wait(timeout=5)
            store.record_request("9.9.9.9", "Mozilla/5.0", _make_entry(ip="9.9.9.9"))
        except BaseException as exc:  # noqa: BLE001 - surfaced via errors list
            errors.append(exc)

    threads = [threading.Thread(target=append_one) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"worker raised: {errors[0]!r}"
    final = store.record_request("9.9.9.9", "Mozilla/5.0", _make_entry(ip="9.9.9.9"))
    assert final.request_count == workers + 1


# --- Capping ---


def test_history_is_capped(store):
    session = _record(store, MAX_SESSION_ENTRIES + 50)
    assert session.request_count == MAX_SESSION_ENTRIES


def test_cap_keeps_the_newest_entries(store):
    _record(store, MAX_SESSION_ENTRIES + 5)
    session = store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry(url="/newest"))
    assert session.requests[-1].url == "/newest"
    assert "/api/endpoint/0" not in [e.url for e in session.requests]


# --- Expiry ---


def test_session_expires(store):
    store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry(), ttl_seconds=1)
    time.sleep(1.1)
    assert store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry()).request_count == 1


def test_every_append_refreshes_the_ttl(store, redis_client):
    key = "live:v2:1.2.3.4"
    store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry(), ttl_seconds=10)
    time.sleep(1.1)
    assert redis_client.ttl(key) < 10
    store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry(), ttl_seconds=10)
    assert redis_client.ttl(key) == 10


# --- Key versioning: the deploy/rollback crossover ---


def test_a_v1_blob_does_not_break_the_v2_store(store, redis_client):
    """Regression: v1 kept a JSON string at live:{ip}; RPUSH on a string is
    WRONGTYPE. Sharing the key name would break every in-flight session on
    deploy, silently, because the entrypoints fail open."""
    redis_client.set("live:1.2.3.4", '{"ip": "1.2.3.4", "requests": []}')
    session = store.record_request("1.2.3.4", "Mozilla/5.0", _make_entry())
    assert session.request_count == 1
    # the v1 key is untouched and expires on its own TTL
    assert redis_client.get("live:1.2.3.4") is not None


# --- Session reconstruction ---


def _entry_at(offset_s, ip="1.2.3.4"):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return _make_entry(ip=ip, timestamp=base + timedelta(seconds=offset_s))


def _raw(entry):
    """The stored form: exactly what record_request RPUSHes."""
    return json.dumps(entry.to_dict())


class TestSessionReconstruction:
    def test_clock_fields_are_floats(self):
        """Regression: LiveSession.start_time/end_time are float epochs. A raw
        datetime makes .duration a timedelta, which raises in labeler.py's
        MIN_RATE_WINDOW_S comparison."""
        session = _session_from(
            "1.2.3.4", "ua", [_raw(_entry_at(0)), _raw(_entry_at(5))]
        )
        assert isinstance(session.start_time, float)
        assert isinstance(session.end_time, float)
        assert isinstance(session.duration, float)

    def test_duration_spans_the_retained_entries(self):
        session = _session_from(
            "1.2.3.4", "ua", [_raw(_entry_at(i)) for i in range(0, 30, 10)]
        )
        assert session.duration == pytest.approx(20.0)

    def test_duration_tracks_the_window_not_the_lifetime(self, store):
        """Regression: after the cap, request_count is pinned. If the clock
        anchored to the session's original start, duration would keep growing
        and the rate rule would read a sustained flood as slower the longer it
        ran. The clock must describe the retained window."""
        for i in range(MAX_SESSION_ENTRIES + 100):
            session = store.record_request("5.5.5.5", "ua", _entry_at(i, ip="5.5.5.5"))
        # 300 requests spanning 299s, trimmed to the last 200 spanning 199s
        assert session.request_count == MAX_SESSION_ENTRIES
        assert session.duration == pytest.approx(MAX_SESSION_ENTRIES - 1)

    def test_empty_history(self):
        session = _session_from("1.2.3.4", "ua", [])
        assert session.request_count == 0
        assert session.duration == 0.0

    def test_one_unreadable_entry_does_not_discard_the_session(self):
        """Scoring a slightly shorter history beats scoring none of it."""
        session = _session_from(
            "1.2.3.4",
            "ua",
            [_raw(_entry_at(0)), "not json", _raw(_entry_at(5))],
        )
        assert session.request_count == 2


# --- Protocol ---


def test_store_implements_protocol(store):
    assert isinstance(store, SessionStateStore)
