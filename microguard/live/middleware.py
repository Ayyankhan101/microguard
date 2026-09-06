"""ASGI + WSGI middleware for real-time bot detection.

Drop-in middleware for FastAPI/Starlette (ASGI) and Flask (WSGI).
Intercepts every request, scores it against live session state,
and blocks bots with 403 while passing humans through.

Usage (FastAPI):
    from microguard.live.middleware import MicroguardASGI
    app = FastAPI()
    app.add_middleware(MicroguardASGI, redis_url="redis://localhost:6379")

Usage (Flask):
    from microguard.live.middleware import MicroguardWSGI
    app = Flask(__name__)
    app.wsgi_app = MicroguardWSGI(app.wsgi_app, redis_url="redis://localhost:6379")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import redis

from ..parser import LogEntry
from .redis_store import RedisSessionStateStore
from .scorer import LiveScorer

# --- ASGI Middleware (FastAPI / Starlette) ---


class MicroguardASGI:
    """ASGI middleware for real-time bot detection.

    Constructor args match `microguard serve` flags:
        redis_url, block_threshold, session_ttl
    """

    def __init__(
        self,
        app: Any,
        redis_url: str = "redis://localhost:6379",
        block_threshold: float = 0.85,
        session_ttl: int = 1800,
        **kwargs: Any,
    ):
        self.app = app
        self._r = redis.Redis.from_url(redis_url, decode_responses=True)
        self._store = RedisSessionStateStore(self._r, default_ttl=session_ttl)
        self._scorer = LiveScorer(
            self._store,
            block_threshold=block_threshold,
            session_ttl=session_ttl,
        )

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract request info from ASGI scope
        headers = dict(scope.get("headers", []))
        ip = _extract_ip(headers)
        ua = _decode_header(headers.get(b"user-agent", b""))
        method = scope.get("method", "GET")
        path = scope.get("path", "/")

        entry = LogEntry(
            ip=ip,
            timestamp=datetime.now(timezone.utc),
            method=method,
            url=path,
            status=0,
            size=0,
            referer="",
            user_agent=ua,
        )

        result = self._scorer.score_request(entry)

        if result["label"] == "bot":
            body = json.dumps(result).encode()
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    [b"x-microguard-label", b"bot"],
                    [b"x-microguard-score", str(result["score"]).encode()],
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})
        else:
            # Add score headers for upstream use, then pass through
            scope["headers"] = list(scope.get("headers", []))
            scope["headers"].append([b"x-microguard-label", b"human"])
            scope["headers"].append([b"x-microguard-score", str(result["score"]).encode()])
            await self.app(scope, receive, send)


# --- WSGI Middleware (Flask) ---


class MicroguardWSGI:
    """WSGI middleware for real-time bot detection.

    Constructor args match `microguard serve` flags:
        redis_url, block_threshold, session_ttl
    """

    def __init__(
        self,
        app: Any,
        redis_url: str = "redis://localhost:6379",
        block_threshold: float = 0.85,
        session_ttl: int = 1800,
        **kwargs: Any,
    ):
        self.app = app
        self._r = redis.Redis.from_url(redis_url, decode_responses=True)
        self._store = RedisSessionStateStore(self._r, default_ttl=session_ttl)
        self._scorer = LiveScorer(
            self._store,
            block_threshold=block_threshold,
            session_ttl=session_ttl,
        )

    def __call__(self, environ: dict, start_response: Any) -> Any:
        ip = (
            environ.get("HTTP_X_REAL_IP")
            or environ.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or environ.get("REMOTE_ADDR", "")
        )
        ua = environ.get("HTTP_USER_AGENT", "")
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")

        entry = LogEntry(
            ip=ip,
            timestamp=datetime.now(timezone.utc),
            method=method,
            url=path,
            status=0,
            size=0,
            referer="",
            user_agent=ua,
        )

        result = self._scorer.score_request(entry)

        if result["label"] == "bot":
            body = json.dumps(result).encode()
            headers = [
                ("X-Microguard-Label", "bot"),
                ("X-Microguard-Score", str(result["score"])),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
            ]
            start_response("403 Forbidden", headers)
            return [body]
        else:
            environ["HTTP_X_MICROGUARD_LABEL"] = "human"
            environ["HTTP_X_MICROGUARD_SCORE"] = str(result["score"])
            return self.app(environ, start_response)


# --- Helpers ---


def _extract_ip(headers: dict) -> str:
    """Extract client IP from ASGI headers."""
    xff = _decode_header(headers.get(b"x-forwarded-for", b""))
    if xff:
        return xff.split(",")[0].strip()
    xri = _decode_header(headers.get(b"x-real-ip", b""))
    if xri:
        return xri.strip()
    return "127.0.0.1"


def _decode_header(value: Any) -> str:
    """Decode ASGI header value (bytes or str) to str."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value else ""
