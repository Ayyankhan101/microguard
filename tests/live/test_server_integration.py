"""Integration tests for the live check server.

Spins up a real HTTP server + Redis, sends real requests.
No mocks — these test actual request handling end-to-end.
"""

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest
import redis


def _free_port():
    """Find a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def redis_client():
    try:
        client = redis.Redis(host="localhost", port=6379, db=14, decode_responses=True)
        client.ping()
        yield client
        client.flushdb()
        client.close()
    except redis.ConnectionError:
        pytest.skip("Redis not available")


@pytest.fixture(scope="module")
def server(redis_client):
    """Start the check server in a subprocess."""
    port = _free_port()
    proc = subprocess.Popen(
        [
            "python", "-m", "microguard.live.server",
            "--port", str(port),
            "--redis-url", "redis://localhost:6379/14",
            "--block-threshold", "0.5",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.5)  # wait for startup
    yield {"proc": proc, "port": port, "redis": redis_client}
    proc.terminate()
    proc.wait(timeout=5)


def _check(port, ip="1.2.3.4", ua="Mozilla/5.0", method="GET", url="/test"):
    """Send a /check request and return (status_code, response_dict)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/check",
        headers={
            "X-Real-IP": ip,
            "User-Agent": ua,
            "X-Original-Method": method,
            "X-Original-URI": url,
        },
    )
    try:
        resp = urllib.request.urlopen(req)
        body = json.loads(resp.read())
        return resp.status, body
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        return e.code, body


class TestCheckServer:
    def test_missing_endpoint_returns_404(self, server):
        req = urllib.request.Request(f"http://127.0.0.1:{server['port']}/missing")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 404

    def test_first_request_always_200(self, server):
        server["redis"].flushdb()
        status, body = _check(server["port"], ip="10.0.0.1")
        assert status == 200
        assert body["label"] == "human"

    def test_bot_eventually_403(self, server):
        server["redis"].flushdb()
        ip = "10.0.0.99"
        # Send enough bot-shaped requests to cross threshold
        for i in range(10):
            status, body = _check(
                server["port"],
                ip=ip,
                ua="python-requests/2.28.0",
                url=f"/wp-admin/{i}",
            )
        assert status == 403
        assert body["label"] == "bot"

    def test_human_session_stays_200(self, server):
        server["redis"].flushdb()
        ip = "10.0.0.50"
        for i in range(5):
            status, _body = _check(
                server["port"],
                ip=ip,
                ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                url=f"/page/{i}",
            )
        assert status == 200

    def test_response_contains_score(self, server):
        server["redis"].flushdb()
        _status, body = _check(server["port"], ip="10.0.0.77")
        assert "score" in body
        assert isinstance(body["score"], float)

    def test_different_ips_independent(self, server):
        server["redis"].flushdb()
        # IP A gets bot traffic
        for i in range(10):
            _check(server["port"], ip="10.0.1.1", ua="curl/7.68", url=f"/scan/{i}")
        # IP B should still be clean
        status, body = _check(server["port"], ip="10.0.1.2", ua="Mozilla/5.0")
        assert status == 200
        assert body["label"] == "human"

    def test_xff_ip_used(self, server):
        server["redis"].flushdb()
        req = urllib.request.Request(
            f"http://127.0.0.1:{server['port']}/check",
            headers={
                "X-Forwarded-For": "10.0.2.99, 10.0.2.100",
                "User-Agent": "Mozilla/5.0",
            },
        )
        resp = urllib.request.urlopen(req)
        assert resp.status == 200
        # The scorer should have used 10.0.2.99 as the client IP
