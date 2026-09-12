"""Tests for threat-intel signals reaching `label_session`.

Every test here asserts the `reason` string as well as the label, because
`label_session` is a first-match-wins chain of ~24 rules: asserting only
`label == 'bot'` passes when a completely different rule fired. That mistake
was made once already across three files in this suite.

The neutral user agent and varied timestamps below exist to avoid tripping
rules 1, 3 and 6 on the way to the rule under test.
"""

from datetime import datetime, timedelta, timezone

import pytest

from microguard.labeler import THREAT_INTEL_ABUSE_THRESHOLD, label_session
from microguard.signals import EMPTY_SIGNALS, Signals

# A real browser UA. An unknown one ("CustomAgent/1.0") trips the
# low-confidence unknown-user-agent rule at 0.6, which would make every
# "does not fire" assertion below pass for the wrong reason.
NEUTRAL_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.0 Safari/605.1.15"
)
BASE = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def neutral_session(make_entry, make_session):
    """A session the existing chain labels human, so a signal rule is what
    fires when one is expected — and nothing fires when none is."""
    entries = [
        make_entry(
            ip="203.0.113.7",
            timestamp=BASE + timedelta(seconds=i * 7 + i * i),
            url=f"/products/{i}",
            user_agent=NEUTRAL_UA,
        )
        for i in range(6)
    ]
    return make_session("203.0.113.7", NEUTRAL_UA, entries)


def _resolved(**kwargs) -> Signals:
    """A resolved Signals with every source promoted unless told otherwise."""
    kwargs.setdefault("promoted", frozenset({"tor", "abuseipdb"}))
    return Signals(resolved=True, **kwargs)


class TestBackwardCompatibility:
    def test_a_caller_passing_no_signals_gets_the_old_behavior(self, neutral_session):
        """REGRESSION: six production call sites and 58 test call sites invoke
        label_session with one argument. The default must change nothing."""
        assert label_session(neutral_session) == label_session(
            neutral_session, EMPTY_SIGNALS
        )

    def test_the_neutral_session_is_not_flagged_without_signals(self, neutral_session):
        label, _confidence, _reason = label_session(neutral_session)
        assert label == "human"


class TestUnresolvedSignalsNeverFire:
    """The batch trap. In `microguard scan` nothing is ever resolved."""

    def test_an_unresolved_tor_hit_does_not_fire(self, neutral_session):
        signals = Signals(resolved=False, tor_exit=True, promoted=frozenset({"tor"}))
        label, _confidence, _reason = label_session(neutral_session, signals)
        assert label == "human"

    def test_an_unresolved_abuse_score_does_not_fire(self, neutral_session):
        signals = Signals(
            resolved=False, abuse_score=100.0, promoted=frozenset({"abuseipdb"})
        )
        label, _confidence, _reason = label_session(neutral_session, signals)
        assert label == "human"


class TestObserveUntilPromoted:
    def test_a_resolved_but_unpromoted_tor_hit_does_not_decide(self, neutral_session):
        """Decision 10A: a new signal is measured before it is enforced."""
        signals = Signals(resolved=True, tor_exit=True, promoted=frozenset())
        label, _confidence, _reason = label_session(neutral_session, signals)
        assert label == "human"

    def test_a_promoted_tor_hit_decides(self, neutral_session):
        label, confidence, reason = label_session(
            neutral_session, _resolved(tor_exit=True)
        )
        assert (label, confidence) == ("bot", 0.85)
        assert "Tor exit node" in reason

    def test_promoting_one_source_does_not_promote_another(self, neutral_session):
        signals = Signals(
            resolved=True,
            tor_exit=True,
            abuse_score=100.0,
            promoted=frozenset({"abuseipdb"}),
        )
        _label, _confidence, reason = label_session(neutral_session, signals)
        assert "Tor exit node" not in reason


class TestAbuseScore:
    def test_fires_at_the_threshold(self, neutral_session):
        signals = _resolved(abuse_score=THREAT_INTEL_ABUSE_THRESHOLD)
        label, confidence, reason = label_session(neutral_session, signals)
        assert (label, confidence) == ("bot", 0.90)
        assert "abuse score" in reason.lower()

    def test_does_not_fire_below_the_threshold(self, neutral_session):
        signals = _resolved(abuse_score=THREAT_INTEL_ABUSE_THRESHOLD - 0.1)
        label, _confidence, _reason = label_session(neutral_session, signals)
        assert label == "human"

    def test_a_missing_score_does_not_fire(self, neutral_session):
        """No API key configured means check_ip returns None, not zero."""
        label, _confidence, _reason = label_session(
            neutral_session, _resolved(abuse_score=None)
        )
        assert label == "human"


class TestHostingRangeIsRecordedNotEnforced:
    def test_a_hosting_range_alone_never_decides(self, neutral_session):
        """Spec 0002 deliberately left the combination rule undecided: a
        datacenter IP is weak evidence and the right combination needs real
        traffic to choose. Until then it is resolved and recorded, never
        enforced — guessing here would invent a false-positive class."""
        signals = _resolved(hosting_range=True, promoted=frozenset({"hosting"}))
        label, _confidence, _reason = label_session(neutral_session, signals)
        assert label == "human"


class TestLocalEvidenceStillWins:
    def test_a_known_bot_user_agent_beats_a_threat_intel_hit(
        self, make_entry, make_session
    ):
        """Chain order is semantics. A local observation this strong must not
        be relabelled by a third-party reputation lookup."""
        entries = [
            make_entry(timestamp=BASE + timedelta(seconds=i * 11), user_agent="Googlebot/2.1")
            for i in range(6)
        ]
        session = make_session("203.0.113.7", "Googlebot/2.1", entries)
        label, confidence, reason = label_session(session, _resolved(tor_exit=True))
        assert (label, confidence) == ("bot", 0.95)
        assert "known bot" in reason.lower()
