"""Tests for ASGI + WSGI middleware.

In-memory store for unit tests. No Redis needed.
Integration tests with Redis are in test_server_integration.py.
"""

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from microguard.live.middleware import MicroguardASGI, MicroguardWSGI
from microguard.live.redis_store import RedisSessionStateStore
from microguard.live.scorer import LiveScorer
from microguard.live.state import LiveSession
from microguard.parser import LogEntry

# --- In-memory store for middleware tests ---


class InMemoryRedis:
    """Mock redis client that behaves like redis-py for our store."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, ex=None):
        self._data[key] = value
        if ex:
            self._ttls[key] = int(time.time()) + ex

    def delete(self, *keys):
        for k in keys:
            self._data.pop(k, None)
            self._ttls.pop(k, None)

    def incrbyfloat(self, key, amount):
        val = float(self._data.get(key, "0.0")) + amount
        self._data[key] = str(val)
        return val

    def expire(self, key, ttl):
        self._ttls[key] = int(time.time()) + ttl

    def pipeline(self):
        return MockPipeline(self)

    def keys(self, pattern="*"):
        return [k.encode() for k in self._data if pattern.replace("*", "") in k]


class MockPipeline:
    def __init__(self, redis_client):
        self._r = redis_client
        self._ops = []

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value, ex))
        return self

    def incrbyfloat(self, key, amount):
        self._ops.append(("incrbyfloat", key, amount))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def delete(self, *keys):
        self._ops.append(("delete", *keys))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "set":
                self._r.set(op[1], op[2], ex=op[3])
            elif op[0] == "incrbyfloat":
                self._r.incrbyfloat(op[1], op[2])
            elif op[0] == "expire":
                self._r.expire(op[1], op[2])
            elif op[0] == "delete":
                self._r.delete(*op[1:])
        self._ops.clear()


def _make_store():
    r = InMemoryRedis()
    return RedisSessionStateStore(r, default_ttl=1800)


def _make_entry(ip="1.2.3.4", ua="Mozilla/5.0", url="/test", method="GET"):
    return LogEntry(
        ip=ip,
        timestamp=datetime.now(timezone.utc),
        method=method,
        url=url,
        status=0,
        size=0,
        referer="",
        user_agent=ua,
    )


# --- ASGI tests ---


def _make_asgi_app(store):
    async def app(scope, receive, send):
        if scope["type"] == "http":
            body = b"OK"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": body})
    return MicroguardASGI(app, redis_url="redis://unused", block_threshold=0.85)


@pytest.fixture()
def asgi_store():
    return _make_store()


class TestASGIMiddleware:
    def test_first_request_passes(self, asgi_store):
        from microguard.live.middleware import MicroguardASGI as MG

        middleware = MG.__new__(MG)
        middleware._store = asgi_store
        middleware._scorer = LiveScorer(asgi_store, block_threshold=0.85)

        async def inner_app(scope, receive, send):
            body = b"OK"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": body})

        middleware.app = inner_app

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "headers": [
                [b"x-real-ip", b"1.2.3.4"],
                [b"user-agent", b"Mozilla/5.0"],
            ],
        }
        sent = []
        async def mock_send(msg):
            sent.append(msg)

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            middleware(scope, MagicMock(), mock_send)
        )
        # First request should pass through — inner app sends 200
        starts = [m for m in sent if m.get("type") == "http.response.start"]
        assert len(starts) == 1
        assert starts[0]["status"] == 200

    def test_bot_request_gets_403(self, asgi_store):
        from microguard.live.middleware import MicroguardASGI as MG

        middleware = MG.__new__(MG)
        middleware._store = asgi_store
        middleware._scorer = LiveScorer(asgi_store, block_threshold=0.85)
        middleware.app = MagicMock()

        # Build up a bot session
        for i in range(10):
            entry = _make_entry(ip="10.0.0.1", ua="python-requests/2.28", url=f"/wp-admin/{i}")
            asgi_store.set("live:10.0.0.1", LiveSession(ip="10.0.0.1", user_agent="python-requests/2.28"), ttl_seconds=1800)
            asgi_store.get("live:10.0.0.1").add_request(entry)  # type: ignore[union-attr]

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/wp-admin/install.php",
            "headers": [
                [b"x-real-ip", b"10.0.0.1"],
                [b"user-agent", b"python-requests/2.28"],
            ],
        }
        sent = []
        async def mock_send(msg):
            sent.append(msg)

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            middleware(scope, MagicMock(), mock_send)
        )
        # Should get a 403 response start
        starts = [m for m in sent if m.get("type") == "http.response.start"]
        assert len(starts) == 1
        assert starts[0]["status"] == 403


# --- WSGI tests ---


def _make_wsgi_store():
    return _make_store()


def _dummy_wsgi_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"OK"]


class TestWSGIMiddleware:
    def test_first_request_passes(self):
        store = _make_store()
        middleware = MicroguardWSGI(_dummy_wsgi_app, redis_url="redis://unused", block_threshold=0.85)
        middleware._store = store  # type: ignore[attr-defined]
        middleware._scorer = LiveScorer(store, block_threshold=0.85)

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/test",
            "HTTP_X_REAL_IP": "1.2.3.4",
            "HTTP_USER_AGENT": "Mozilla/5.0",
            "REMOTE_ADDR": "127.0.0.1",
        }
        responses = []
        def start_response(status, headers):
            responses.append((status, headers))

        _result = middleware(environ, start_response)
        assert responses[0][0] == "200 OK"

    def test_bot_request_gets_403(self):
        store = _make_store()
        middleware = MicroguardWSGI(_dummy_wsgi_app, redis_url="redis://unused", block_threshold=0.85)
        middleware._store = store  # type: ignore[attr-defined]
        middleware._scorer = LiveScorer(store, block_threshold=0.85)

        # Build up bot session
        for i in range(10):
            session = LiveSession(ip="10.0.0.1", user_agent="curl/7.68")
            entry = _make_entry(ip="10.0.0.1", ua="curl/7.68", url=f"/scan/{i}")
            session.add_request(entry)
            store.set("live:10.0.0.1", session, ttl_seconds=1800)

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/scan/10",
            "HTTP_USER_AGENT": "curl/7.68",
            "REMOTE_ADDR": "10.0.0.1",
        }
        responses = []
        def start_response(status, headers):
            responses.append((status, headers))

        result = middleware(environ, start_response)
        assert responses[0][0] == "403 Forbidden"
        body = json.loads(b"".join(result))
        assert body["label"] == "bot"

    def test_human_gets_200_with_headers(self):
        store = _make_store()
        middleware = MicroguardWSGI(_dummy_wsgi_app, redis_url="redis://unused", block_threshold=0.85)
        middleware._store = store  # type: ignore[attr-defined]
        middleware._scorer = LiveScorer(store, block_threshold=0.85)

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/page",
            "HTTP_USER_AGENT": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "REMOTE_ADDR": "1.2.3.4",
        }
        responses = []
        def start_response(status, headers):
            responses.append((status, headers))

        _result = middleware(environ, start_response)
        assert responses[0][0] == "200 OK"
        # Check that microguard headers were added to environ
        assert environ.get("HTTP_X_MICROGUARD_LABEL") == "human"

    def test_xff_ip_extracted(self):
        store = _make_store()
        middleware = MicroguardWSGI(_dummy_wsgi_app, redis_url="redis://unused", block_threshold=0.85)
        middleware._store = store  # type: ignore[attr-defined]
        middleware._scorer = LiveScorer(store, block_threshold=0.85)

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/test",
            "HTTP_X_FORWARDED_FOR": "10.0.0.99, 10.0.0.100",
            "HTTP_USER_AGENT": "Mozilla/5.0",
            "REMOTE_ADDR": "127.0.0.1",
        }
        responses = []
        def start_response(status, headers):
            responses.append((status, headers))

        middleware(environ, start_response)
        # Verify the scorer used the XFF IP (session should exist for 10.0.0.99)
        session = store.get("live:10.0.0.99")
        assert session is not None


# --- Constructor signature ---


class TestConstructorRejectsUnknownKwargs:
    """A mistyped knob must fail loudly, not fall back to the default threshold."""

    def test_asgi_rejects_unknown_kwarg(self):
        async def app(scope, receive, send):
            pass

        with pytest.raises(TypeError):
            MicroguardASGI(app, block_treshold=0.5)  # typo, must not be swallowed

    def test_wsgi_rejects_unknown_kwarg(self):
        with pytest.raises(TypeError):
            MicroguardWSGI(_dummy_wsgi_app, block_treshold=0.5)
