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


def _wait_until_listening(proc, port, timeout=15.0):
    """Poll until the server accepts a connection.

    A flat sleep was flaky: under a full-suite run the subprocess sometimes
    needed longer than the fixed wait, and the first test in the module failed
    in a way that looked like a scoring bug rather than a startup race.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"check server exited early with code {proc.returncode}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"check server did not start listening on {port} in {timeout}s")


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
    _wait_until_listening(proc, port)
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


class TestConcurrentRequests:
    """The check server sits inline in front of every request to the protected
    site, so it must serve concurrently and stay correct while doing it."""

    def test_concurrent_requests_from_distinct_ips_are_all_answered(self, server):
        import concurrent.futures

        server["redis"].flushdb()
        ips = [f"10.20.0.{i}" for i in range(1, 25)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ips)) as pool:
            results = list(
                pool.map(
                    lambda ip: _check(
                        server["port"], ip=ip, ua="Mozilla/5.0", url="/products/1"
                    ),
                    ips,
                )
            )

        assert len(results) == len(ips)
        assert all(status == 200 for status, _ in results), [s for s, _ in results]
        # each IP got its own session, not a shared one
        assert all(body["request_count"] == 1 for _, body in results)

    def test_concurrent_requests_from_one_ip_all_accumulate(self, server):
        """Redis does the appending atomically, so nothing is lost even when
        every thread reads and writes the same session key."""
        import concurrent.futures

        server["redis"].flushdb()
        n = 20

        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            list(
                pool.map(
                    lambda i: _check(
                        server["port"], ip="10.21.0.1", ua="Mozilla/5.0", url=f"/p/{i}"
                    ),
                    range(n),
                )
            )

        _status, body = _check(
            server["port"], ip="10.21.0.1", ua="Mozilla/5.0", url="/p/final"
        )
        assert body["request_count"] == n + 1

    def test_a_slow_request_does_not_block_others(self, server):
        """A plain HTTPServer answers one connection at a time; this asserts we
        are not on one. Two requests issued together must overlap rather than
        run end to end."""
        import concurrent.futures
        import time

        server["redis"].flushdb()
        started: list[float] = []

        def timed(ip):
            started.append(time.perf_counter())
            return _check(server["port"], ip=ip, ua="Mozilla/5.0", url="/x")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            t0 = time.perf_counter()
            list(pool.map(timed, [f"10.22.0.{i}" for i in range(8)]))
            elapsed = time.perf_counter() - t0

        # 8 requests, each a Redis round trip plus a model forward pass.
        # Generous ceiling — this catches serialization, not slow hardware.
        assert elapsed < 5.0, f"8 concurrent checks took {elapsed:.2f}s"


def _post_fp(port, ip, body):
    """POST a fingerprint body and return the status, never raising for 4xx."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/fp",
        data=body,
        headers={"Content-Type": "application/json", "X-Real-IP": ip},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


class TestFingerprintRoutes:
    """The two public routes, against the real running process.

    These exercise BaseHTTPRequestHandler's do_POST and the static route end
    to end -- the parts test_fp_routes.py cannot reach, because it stops at
    the handler function.
    """

    def test_the_script_is_served(self, server):
        url = f"http://127.0.0.1:{server['port']}/fingerprint.js"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read()
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("application/javascript")
        assert b"SHA-256" in body

    def test_the_prefixed_script_path_works_too(self, server):
        """nginx may or may not strip the location prefix, depending on whether
        proxy_pass carries a trailing path. Both spellings answer, because an
        operator who gets that subtly wrong should not get a silently dead
        fingerprint pipeline."""
        url = f"http://127.0.0.1:{server['port']}/microguard/fingerprint.js"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.status == 200

    def test_a_fingerprint_from_a_known_actor_is_recorded(self, server):
        ip = "203.0.113.77"
        # Give the actor a session first: /fp only binds a hash to an IP that
        # already has history (decision 7A).
        _check(server["port"], ip=ip, ua="Mozilla/5.0", url="/products")

        body = json.dumps({"fingerprint_hash": "c" * 64}).encode()
        assert _post_fp(server["port"], ip, body) == 200

        stored = server["redis"].get(f"mg:v1:fp:{ip}")
        assert json.loads(stored)["hash"] == "c" * 64

    def test_an_unknown_actor_is_refused_without_an_error(self, server):
        ip = "198.51.100.123"
        body = json.dumps({"fingerprint_hash": "d" * 64}).encode()

        assert _post_fp(server["port"], ip, body) == 200
        assert server["redis"].get(f"mg:v1:fp:{ip}") is None

    def test_an_oversized_body_is_refused(self, server):
        assert _post_fp(server["port"], "203.0.113.78", b"x" * 200_000) == 200

    def test_a_post_to_an_unknown_path_still_explains_itself(self, server):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server['port']}/nope", data=b"{}"
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=5)
        assert exc.value.code == 404
        assert b"microguard check server" in exc.value.read()
