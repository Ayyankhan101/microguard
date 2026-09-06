"""nginx auth_request microservice — standalone HTTP server.

Handles /check requests from nginx auth_request module.
Returns 200 (allow) or 403 (block) based on live scoring.

nginx config example:
    location /api/ {
        auth_request /_microguard_check;
        auth_request_set $microguard_label $upstream_http_x_microguard_label;
        proxy_set_header X-Microguard-Label $microguard_label;
        proxy_pass http://backend;
    }

    location = /_microguard_check {
        internal;
        proxy_pass http://127.0.0.1:8400/check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Original-URI $request_uri;
        proxy_set_header X-Original-Method $request_method;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header User-Agent $http_user_agent;
    }
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import redis

from ..parser import LogEntry
from .redis_store import RedisSessionStateStore
from .scorer import LiveScorer


class CheckHandler(BaseHTTPRequestHandler):
    """Handle /check requests from nginx auth_request."""

    scorer: LiveScorer

    def do_GET(self):
        if self.path != "/check":
            self.send_error(404)
            return

        # Extract client IP from X-Real-IP or X-Forwarded-For
        ip = self._get_client_ip()
        ua = self.headers.get("User-Agent", "")
        method = self.headers.get("X-Original-Method", "GET")
        url = self.headers.get("X-Original-URI", "/")

        entry = LogEntry(
            ip=ip,
            timestamp=datetime.now(timezone.utc),
            method=method,
            url=url,
            status=0,
            size=0,
            referer="",
            user_agent=ua,
        )

        result = self.scorer.score_request(entry)

        if result["label"] == "bot":
            self.send_response(403)
            self.send_header("X-Microguard-Label", "bot")
            self.send_header("X-Microguard-Score", str(result["score"]))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        else:
            self.send_response(200)
            self.send_header("X-Microguard-Label", "human")
            self.send_header("X-Microguard-Score", str(result["score"]))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

    def _get_client_ip(self) -> str:
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        xri = self.headers.get("X-Real-IP", "")
        if xri:
            return xri.strip()
        return self.client_address[0]

    def log_message(self, format, *args):
        """Suppress default logging — let the caller decide."""


def run_server(
    host: str = "127.0.0.1",
    port: int = 8400,
    redis_url: str = "redis://localhost:6379",
    block_threshold: float = 0.85,
    session_ttl: int = 1800,
):
    """Start the check server."""
    r = redis.Redis.from_url(redis_url, decode_responses=True)
    r.ping()
    store = RedisSessionStateStore(r, default_ttl=session_ttl)
    scorer = LiveScorer(store, block_threshold=block_threshold)

    CheckHandler.scorer = scorer
    server = HTTPServer((host, port), CheckHandler)
    print(f"microguard check server listening on {host}:{port}")
    print(f"  redis: {redis_url}")
    print(f"  threshold: {block_threshold}")
    print(f"  session TTL: {session_ttl}s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Microguard live check server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8400)
    parser.add_argument("--redis-url", default="redis://localhost:6379")
    parser.add_argument("--block-threshold", type=float, default=0.85)
    parser.add_argument("--session-ttl", type=int, default=1800)
    args = parser.parse_args()
    run_server(
        host=args.host,
        port=args.port,
        redis_url=args.redis_url,
        block_threshold=args.block_threshold,
        session_ttl=args.session_ttl,
    )


if __name__ == "__main__":
    main()
