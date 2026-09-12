"""Tests for the fingerprint rules in `label_session`.

Rule 1 infers automation from an ABSENT fingerprint, which makes it the most
dangerous rule in this file: absence is also what a batch scan, an API client,
and a site that never embedded the script all look like. Most of these tests
exist to pin the cases where it must stay silent.
"""

from datetime import datetime, timedelta, timezone

import pytest

from microguard.labeler import (
    FINGERPRINT_GRACE_SECONDS,
    FINGERPRINT_MIN_REQUESTS,
    SHARED_FINGERPRINT_IPS,
    label_session,
)
from microguard.signals import Signals

BASE = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.0 Safari/605.1.15"
)
PROMOTED = frozenset({"fingerprint"})


@pytest.fixture()
def page_session(make_entry, make_session):
    """A browser walking pages, slowly enough not to trip the rate rules."""
    entries = [
        make_entry(ip="203.0.113.7", timestamp=BASE + timedelta(seconds=i * 9),
                   url=f"/products/{i}", user_agent=BROWSER_UA)
        for i in range(FINGERPRINT_MIN_REQUESTS + 1)
    ]
    return make_session("203.0.113.7", BROWSER_UA, entries)


@pytest.fixture()
def api_session(make_entry, make_session):
    """A REST client. It has no reason to ever execute page JavaScript."""
    entries = [
        make_entry(ip="203.0.113.7", timestamp=BASE + timedelta(seconds=i * 9),
                   url=f"/api/v1/orders/{i}", user_agent=BROWSER_UA)
        for i in range(FINGERPRINT_MIN_REQUESTS + 1)
    ]
    return make_session("203.0.113.7", BROWSER_UA, entries)


def _fp(**kwargs) -> Signals:
    kwargs.setdefault("promoted", PROMOTED)
    kwargs.setdefault("fp_resolved", True)
    return Signals(**kwargs)


class TestAbsenceIsNotEvidenceUnlessItWasMeasured:
    def test_an_unresolved_fingerprint_never_fires(self, page_session):
        """The batch trap. `microguard scan` never consults the fingerprint
        pipeline, so every session in every log file would look automated."""
        signals = Signals(fp_resolved=False, promoted=PROMOTED)
        assert label_session(page_session, signals)[0] == "human"

    def test_the_default_signals_never_fire(self, page_session):
        assert label_session(page_session)[0] == "human"

    def test_an_unpromoted_absence_does_not_decide(self, page_session):
        """Observe-only: measured, recorded, not enforced."""
        assert label_session(page_session, Signals(fp_resolved=True))[0] == "human"


class TestGracePeriod:
    def test_a_page_session_with_no_fingerprint_is_flagged(self, page_session):
        label, confidence, reason = label_session(page_session, _fp())
        assert label == "bot"
        assert confidence == 0.80
        assert "no fingerprint" in reason.lower()

    def test_an_api_only_session_is_exempt(self, api_session):
        """A REST or GraphQL client never loads a page, so it never runs the
        script. Flagging it would reintroduce the single-endpoint-API false
        positive this project already spent real work removing."""
        assert label_session(api_session, _fp())[0] == "human"

    def test_a_session_shorter_than_the_grace_period_is_not_flagged(
        self, make_entry, make_session
    ):
        """A real browser POSTs within milliseconds, but a slow connection or
        a deferred script needs room before absence means anything."""
        entries = [
            make_entry(ip="203.0.113.7",
                       timestamp=BASE + timedelta(seconds=i * 0.1),
                       url=f"/products/{i}", user_agent=BROWSER_UA)
            for i in range(FINGERPRINT_MIN_REQUESTS + 1)
        ]
        session = make_session("203.0.113.7", BROWSER_UA, entries)
        assert session.duration < FINGERPRINT_GRACE_SECONDS
        assert label_session(session, _fp())[0] == "human"

    def test_too_few_requests_is_not_enough(self, make_entry, make_session):
        entries = [
            make_entry(ip="203.0.113.7", timestamp=BASE + timedelta(seconds=i * 30),
                       url=f"/products/{i}", user_agent=BROWSER_UA)
            for i in range(FINGERPRINT_MIN_REQUESTS - 1)
        ]
        session = make_session("203.0.113.7", BROWSER_UA, entries)
        assert label_session(session, _fp())[0] == "human"

    def test_a_present_fingerprint_clears_the_rule(self, page_session):
        assert label_session(page_session, _fp(fingerprint_hash="a" * 64))[0] == "human"


class TestSharedFingerprint:
    def test_a_hash_shared_across_enough_ips_is_flagged(self, page_session):
        signals = _fp(fingerprint_hash="a" * 64, shared_hash_ips=SHARED_FINGERPRINT_IPS)
        label, confidence, reason = label_session(page_session, signals)
        assert label == "bot"
        assert confidence == 0.90
        assert "distinct IPs" in reason

    def test_below_the_threshold_it_does_not_fire(self, page_session):
        signals = _fp(fingerprint_hash="a" * 64, shared_hash_ips=SHARED_FINGERPRINT_IPS - 1)
        assert label_session(page_session, signals)[0] == "human"

    def test_it_fires_even_for_an_api_only_session(self, api_session):
        """Unlike rule 1, this is positive evidence rather than an inference
        from absence, so the page-route exemption does not apply."""
        signals = _fp(fingerprint_hash="a" * 64, shared_hash_ips=SHARED_FINGERPRINT_IPS)
        assert label_session(api_session, signals)[0] == "bot"

    def test_an_unpromoted_shared_hash_does_not_decide(self, page_session):
        signals = Signals(fp_resolved=True, fingerprint_hash="a" * 64,
                          shared_hash_ips=SHARED_FINGERPRINT_IPS)
        assert label_session(page_session, signals)[0] == "human"
