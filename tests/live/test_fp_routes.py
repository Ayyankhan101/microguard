"""Tests for the /fp and /fingerprint.js handlers.

These cover the host-agnostic layer that all three deployment hosts call. The
cross-host contract test lives in test_fp_contract.py; this file pins the
behavior that contract is written against.
"""

import json
from datetime import datetime, timezone

import pytest
import redis

from microguard.live.fp_routes import (
    MAX_FP_BODY_BYTES,
    fingerprint_script,
    handle_fp_post,
)
from microguard.live.redis_store import RedisSessionStateStore
from microguard.parser import LogEntry

HASH_A = "a" * 64


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


def _seed(client, ip="203.0.113.5"):
    RedisSessionStateStore(client, default_ttl=300).record_request(
        ip, "Mozilla/5.0",
        LogEntry(ip=ip, timestamp=datetime(2023, 3, 24, tzinfo=timezone.utc),
                 method="GET", url="/", status=200, size=1, referer="",
                 user_agent="Mozilla/5.0"),
    )


def _body(hash_value=HASH_A):
    return json.dumps({"fingerprint_hash": hash_value}).encode()


class TestAcceptedSubmission:
    def test_a_bound_submission_is_recorded(self, clean):
        _seed(clean)
        status, _payload = handle_fp_post(clean, "203.0.113.5", _body())
        assert status == 200
        assert json.loads(clean.get("mg:v1:fp:203.0.113.5"))["hash"] == HASH_A


class TestEveryRejectionStillAnswers200:
    """`/fp` is public, non-critical, and unauthenticated.

    A 4xx or 5xx here would do two bad things: hand an attacker an oracle for
    which IPs are bindable, and surface a microguard problem as an error on
    someone else's page. Rejections are silent and the visitor never knows.
    """

    @pytest.mark.parametrize(
        "body",
        [b"", b"not json", b"[]", b"{}", json.dumps({"fingerprint_hash": "nope"}).encode()],
        ids=["empty", "not-json", "not-object", "missing-field", "bad-hash"],
    )
    def test_malformed_bodies_are_refused_quietly(self, clean, body):
        _seed(clean)
        status, _payload = handle_fp_post(clean, "203.0.113.5", body)
        assert status == 200
        assert clean.get("mg:v1:fp:203.0.113.5") is None

    def test_an_oversized_body_is_refused_before_parsing(self, clean):
        _seed(clean)
        status, _payload = handle_fp_post(clean, "203.0.113.5", b"x" * (MAX_FP_BODY_BYTES + 1))
        assert status == 200
        assert clean.get("mg:v1:fp:203.0.113.5") is None

    def test_an_unbound_actor_is_refused(self, clean):
        status, _payload = handle_fp_post(clean, "198.51.100.9", _body())
        assert status == 200
        assert clean.get("mg:v1:fp:198.51.100.9") is None

    def test_a_redis_failure_still_answers_200(self, caplog):
        """The route sits in the process nginx depends on. An exception here
        must not become a 500 on the visitor's page."""
        class Broken:
            def exists(self, *a):
                raise redis.ConnectionError("gone")

        status, _payload = handle_fp_post(Broken(), "203.0.113.5", _body())
        assert status == 200
        assert "fingerprint recording failed" in caplog.text

    def test_the_response_body_never_says_which_rejection_happened(self, clean):
        """Same bytes for accept and reject: an attacker probing for a
        bindable IP learns nothing from the response."""
        _seed(clean)
        accepted = handle_fp_post(clean, "203.0.113.5", _body())
        rejected = handle_fp_post(clean, "198.51.100.9", _body())
        assert accepted == rejected


class TestScript:
    def test_it_is_served_as_javascript(self):
        body, content_type = fingerprint_script()
        assert content_type.startswith("application/javascript")
        assert b"microguard" in body

    def test_it_only_ever_transmits_a_hash(self):
        """The privacy claim, checked against the source that makes it.

        The Playwright test inspects the real network payload; this catches
        the regression earlier and without a browser.
        """
        body, _ = fingerprint_script()
        source = body.decode()
        assert "fingerprint_hash: hash" in source
        assert source.count("JSON.stringify") == 1
        assert "SHA-256" in source

    def test_it_is_cached_after_the_first_read(self):
        first, _ = fingerprint_script()
        second, _ = fingerprint_script()
        assert first is second
