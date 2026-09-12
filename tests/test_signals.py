"""Tests for the signal seam — the shape `label_session` accepts.

This module is stdlib-only on purpose. `microguard/live/__init__.py` raises
ImportError without redis-py, so anything `labeler.py` imports must live
outside `live/` or `microguard scan` stops working on a base install. The same
split already exists for `events.py` vs `live/redis_events.py`.

The import test at the bottom is the one that would actually catch a
regression there.
"""

import subprocess
import sys

from microguard.signals import EMPTY_SIGNALS, Signals


class TestEmptySignals:
    def test_is_unresolved(self):
        """The default must mean 'nothing was looked up', not 'nothing found'.

        A rule reading the difference is the whole point: in a batch
        `microguard scan` no signal is ever resolved, so a rule that treats
        absence as evidence would label every session in every log file.
        """
        assert EMPTY_SIGNALS.resolved is False

    def test_carries_no_verdicts(self):
        assert EMPTY_SIGNALS.tor_exit is False
        assert EMPTY_SIGNALS.hosting_range is False
        assert EMPTY_SIGNALS.abuse_score is None

    def test_promotes_nothing_by_default(self):
        """Observe-only is the default posture for every new signal."""
        assert EMPTY_SIGNALS.promoted == frozenset()

    def test_is_frozen(self):
        import dataclasses

        try:
            EMPTY_SIGNALS.tor_exit = True
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("EMPTY_SIGNALS must be immutable — it is a shared default")

    def test_is_a_singleton_callers_can_compare_against(self):
        assert EMPTY_SIGNALS == Signals()


class TestPromotion:
    def test_reports_a_promoted_source(self):
        signals = Signals(resolved=True, promoted=frozenset({"tor"}))
        assert signals.is_promoted("tor") is True

    def test_reports_an_unpromoted_source(self):
        signals = Signals(resolved=True, promoted=frozenset({"tor"}))
        assert signals.is_promoted("abuseipdb") is False

    def test_unresolved_signals_promote_nothing(self):
        """Even an explicitly promoted source cannot act on data that was
        never looked up."""
        signals = Signals(resolved=False, promoted=frozenset({"tor"}))
        assert signals.is_promoted("tor") is False


class TestPayloadParsing:
    def test_a_mapping_becomes_resolved_signals(self):
        from microguard.signals import signals_from_payload

        signals = signals_from_payload({"tor_exit": True, "abuse_score": 80.0})
        assert signals.resolved is True
        assert signals.tor_exit is True
        assert signals.abuse_score == 80.0

    def test_a_non_mapping_payload_reads_as_unresolved(self):
        """Redis hands back whatever was written. A list, a bare string or a
        null where an object was expected must read as 'nothing was looked up',
        not raise on the request path."""
        from microguard.signals import signals_from_payload

        assert signals_from_payload(["tor_exit"]) is EMPTY_SIGNALS
        assert signals_from_payload(None) is EMPTY_SIGNALS
        assert signals_from_payload("tor_exit") is EMPTY_SIGNALS


class TestImportIsolation:
    def test_importing_signals_does_not_pull_in_redis(self):
        """The seam's guarantee, checked mechanically.

        `labeler.py` imports this module, and `labeler.py` is on the
        `microguard scan` path, which must work on a base install with no
        extras. A fresh interpreter is used because redis may already be
        imported by another test in this process.
        """
        code = (
            "import microguard.signals, sys; "
            "sys.exit(1 if 'redis' in sys.modules else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, check=False
        )
        assert result.returncode == 0, "importing microguard.signals pulled in redis"

    def test_importing_labeler_does_not_pull_in_redis(self):
        code = (
            "import microguard.labeler, sys; "
            "sys.exit(1 if 'redis' in sys.modules else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, check=False
        )
        assert result.returncode == 0, "importing microguard.labeler pulled in redis"

    def test_importing_the_dashboard_api_does_not_pull_in_redis(self):
        """The dashboard has to run on a base install.

        This nearly regressed: the promotion control needs KNOWN_SIGNAL_SOURCES
        to render, and importing it from live/runtime_config.py would have
        dragged the whole live package -- and its redis guard -- behind the
        dashboard's scan and model tabs.
        """
        code = (
            "import microguard.dashboard.api_live, sys; "
            "sys.exit(1 if 'redis' in sys.modules else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, check=False
        )
        assert result.returncode == 0, "importing the dashboard API pulled in redis"
