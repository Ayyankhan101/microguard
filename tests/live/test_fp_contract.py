"""The three /fp hosts must answer identically.

Decision E1A requires the fingerprint routes to work in all three deployment
modes, which means the handler is reached three different ways: through
BaseHTTPRequestHandler, through the ASGI protocol, and through WSGI. The real
risk is not that one of them is wrong in isolation — each has its own tests —
but that they drift. This asserts they agree on the same inputs.
"""

import json
from datetime import datetime, timezone

import pytest
import redis

from microguard.live.redis_store import RedisSessionStateStore
from microguard.parser import LogEntry

HASH_A = "a" * 64
BODY = json.dumps({"fingerprint_hash": HASH_A}).encode()


@pytest.fixture(scope="module")
def redis_client():
    try:
        c = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        c.ping()
        yield c
        c.flushdb()
        c.close()
    except redis.ConnectionError:
        pytest.skip("Redis not available on localhost:6379")


@pytest.fixture()
def clean(redis_client):
    redis_client.flushdb()
    return redis_client


def _seed(client, ip):
    RedisSessionStateStore(client, default_ttl=300).record_request(
        ip, "Mozilla/5.0",
        LogEntry(ip=ip, timestamp=datetime(2023, 3, 24, tzinfo=timezone.utc),
                 method="GET", url="/", status=200, size=1, referer="",
                 user_agent="Mozilla/5.0"),
    )


def _via_asgi(client, ip, path, body, method="POST"):
    import asyncio

    from microguard.live.middleware import MicroguardASGI

    async def app(scope, receive, send):  # pragma: no cover - must never run
        raise AssertionError("fingerprint routes must not reach the wrapped app")

    mw = MicroguardASGI.__new__(MicroguardASGI)
    mw.app = app
    mw._r = client
    mw._trust_xff = False

    sent = []
    chunks = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return chunks.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": path, "method": method,
             "headers": [], "client": (ip, 1234)}
    # Not asyncio.run(): it closes the loop AND clears the thread's current
    # loop, which breaks the older get_event_loop() pattern in
    # test_middleware.py when these run in the same process.
    previous = asyncio.get_event_loop_policy().get_event_loop()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(mw(scope, receive, send))
    finally:
        loop.close()
        asyncio.set_event_loop(previous)
    start = sent[0]
    return start["status"], sent[1]["body"], {
        k.decode().lower(): v.decode() for k, v in start["headers"]
    }


def _via_wsgi(client, ip, path, body, method="POST"):
    import io

    from microguard.live.middleware import MicroguardWSGI

    def app(environ, start_response):  # pragma: no cover - must never run
        raise AssertionError("fingerprint routes must not reach the wrapped app")

    mw = MicroguardWSGI.__new__(MicroguardWSGI)
    mw.app = app
    mw._r = client
    mw._trust_xff = False

    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
        captured["headers"] = {k.lower(): v for k, v in headers}

    environ = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "REMOTE_ADDR": ip,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    result = b"".join(mw(environ, start_response))
    return captured["status"], result, captured["headers"]


def _via_handler(client, ip, path, body, method="POST"):
    from microguard.live.fp_routes import fingerprint_script, handle_fp_post

    if method == "GET":
        script, content_type = fingerprint_script()
        return 200, script, {"content-type": content_type}
    status, payload = handle_fp_post(client, ip, body)
    return status, payload, {"content-type": "application/json"}


HOSTS = {"handler": _via_handler, "asgi": _via_asgi, "wsgi": _via_wsgi}


class TestAllHostsAgree:
    @pytest.mark.parametrize("path", ["/fp", "/microguard/fp"])
    def test_an_accepted_submission_binds_the_hash_in_every_host(self, clean, path):
        for name, call in HOSTS.items():
            clean.flushdb()
            _seed(clean, "203.0.113.5")
            status, body, headers = call(clean, "203.0.113.5", path, BODY)

            assert status == 200, name
            assert body == b'{"ok":true}', name
            assert headers["content-type"] == "application/json", name
            bound = json.loads(clean.get("mg:v1:fp:203.0.113.5"))["hash"]
            assert bound == HASH_A, name

    def test_an_unbound_actor_is_refused_identically_everywhere(self, clean):
        results = []
        for call in HOSTS.values():
            clean.flushdb()
            results.append(call(clean, "198.51.100.9", "/fp", BODY))
            assert clean.get("mg:v1:fp:198.51.100.9") is None

        assert len({(s, b) for s, b, _ in results}) == 1

    def test_a_malformed_body_is_refused_identically_everywhere(self, clean):
        results = []
        for call in HOSTS.values():
            clean.flushdb()
            _seed(clean, "203.0.113.5")
            results.append(call(clean, "203.0.113.5", "/fp", b"not json"))

        assert len({(s, b) for s, b, _ in results}) == 1

    def test_an_oversized_body_is_refused_identically_everywhere(self, clean):
        huge = b"x" * 9000
        results = []
        for call in HOSTS.values():
            clean.flushdb()
            _seed(clean, "203.0.113.5")
            results.append(call(clean, "203.0.113.5", "/fp", huge))
            assert clean.get("mg:v1:fp:203.0.113.5") is None

        assert len({(s, b) for s, b, _ in results}) == 1

    @pytest.mark.parametrize("path", ["/fingerprint.js", "/microguard/fingerprint.js"])
    def test_every_host_serves_the_same_script(self, clean, path):
        bodies = {
            name: call(clean, "203.0.113.5", path, b"", method="GET")[1]
            for name, call in HOSTS.items()
        }
        assert len(set(bodies.values())) == 1
        assert b"SHA-256" in next(iter(bodies.values()))


class TestWsgiBodyLimits:
    """The WSGI adapter reads its own body, so its limits need their own test.

    The ASGI side stops on `more_body`; this side trusts CONTENT_LENGTH, which
    is client-supplied and therefore not trusted at all.
    """

    def test_a_junk_content_length_is_treated_as_zero(self, clean):
        import io

        from microguard.live.middleware import MicroguardWSGI

        mw = MicroguardWSGI.__new__(MicroguardWSGI)
        mw.app = None
        mw._r = clean
        mw._trust_xff = False

        captured = {}

        def start_response(status, headers):
            captured["status"] = int(status.split()[0])

        _seed(clean, "203.0.113.5")
        body = b"".join(mw({
            "PATH_INFO": "/fp",
            "REQUEST_METHOD": "POST",
            "REMOTE_ADDR": "203.0.113.5",
            "CONTENT_LENGTH": "not-a-number",
            "wsgi.input": io.BytesIO(BODY),
        }, start_response))

        assert captured["status"] == 200
        assert body == b'{"ok":true}'
        # Nothing was read, so nothing bound.
        assert clean.get("mg:v1:fp:203.0.113.5") is None
