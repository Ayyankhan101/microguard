"""Tests for ASGI + WSGI middleware.

In-memory store for unit tests. No Redis needed.
Integration tests with Redis are in test_server_integration.py.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from microguard.live.middleware import MicroguardASGI, MicroguardWSGI
from microguard.live.scorer import LiveScorer
from microguard.parser import LogEntry

from .conftest import InMemoryStore


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


@pytest.fixture()
def asgi_store():
    return InMemoryStore()


# --- ASGI tests ---


def _make_asgi_app(store):
    async def app(scope, receive, send):
        if scope["type"] == "http":
            body = b"OK"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": body})
    return MicroguardASGI(app, redis_url="redis://unused", block_threshold=0.85)


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
            asgi_store.record_request("10.0.0.1", "python-requests/2.28", entry)

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


def _dummy_wsgi_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"OK"]


class TestWSGIMiddleware:
    def test_first_request_passes(self):
        store = InMemoryStore()
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
        store = InMemoryStore()
        middleware = MicroguardWSGI(_dummy_wsgi_app, redis_url="redis://unused", block_threshold=0.85)
        middleware._store = store  # type: ignore[attr-defined]
        middleware._scorer = LiveScorer(store, block_threshold=0.85)

        # Build up bot session through the real append path
        for i in range(10):
            store.record_request(
                "10.0.0.1",
                "curl/7.68",
                _make_entry(ip="10.0.0.1", ua="curl/7.68", url=f"/scan/{i}"),
            )

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
        store = InMemoryStore()
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

    def _run(self, environ, trust_forwarded_for=False):
        store = InMemoryStore()
        middleware = MicroguardWSGI(
            _dummy_wsgi_app,
            redis_url="redis://unused",
            block_threshold=0.85,
            trust_forwarded_for=trust_forwarded_for,
        )
        middleware._store = store  # type: ignore[attr-defined]
        middleware._scorer = LiveScorer(store, block_threshold=0.85)
        middleware(environ, lambda status, headers: None)
        return store

    def test_xff_ignored_by_default(self):
        """X-Forwarded-For is client-supplied; trusting it lets a bot rotate sessions."""
        store = self._run({
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/test",
            "HTTP_X_FORWARDED_FOR": "10.0.0.99, 10.0.0.100",
            "HTTP_USER_AGENT": "Mozilla/5.0",
            "REMOTE_ADDR": "127.0.0.1",
        })
        assert store.peek("10.0.0.99") is None
        assert store.peek("127.0.0.1") is not None

    def test_xff_used_when_trusted(self):
        store = self._run({
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/test",
            "HTTP_X_FORWARDED_FOR": "10.0.0.99, 10.0.0.100",
            "HTTP_USER_AGENT": "Mozilla/5.0",
            "REMOTE_ADDR": "127.0.0.1",
        }, trust_forwarded_for=True)
        assert store.peek("10.0.0.99") is not None

    def test_scoring_failure_fails_open(self):
        """A Redis outage must not 500 every request through the app."""
        store = InMemoryStore()
        middleware = MicroguardWSGI(
            _dummy_wsgi_app, redis_url="redis://unused", block_threshold=0.85
        )
        middleware._store = store  # type: ignore[attr-defined]
        middleware._scorer = MagicMock()
        middleware._scorer.score_request.side_effect = RuntimeError("redis down")

        responses = []
        middleware({
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/test",
            "REMOTE_ADDR": "127.0.0.1",
        }, lambda status, headers: responses.append((status, headers)))
        assert responses[0][0] == "200 OK"


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
