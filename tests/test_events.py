"""Tests for decision recording — the live dashboard's data source.

InMemoryDecisionRecorder lives at microguard/events.py rather than under
live/, because live/__init__.py raises ImportError without the 'live' extra
and the dashboard must run without Redis. This module must therefore import
cleanly on a bare install; that is part of what these tests pin.
"""

import pytest

from microguard.events import InMemoryDecisionRecorder


def decision(ip="10.0.0.1", label="human", score=0.1, **overrides):
    """A LiveScorer.score_request() payload (live/scorer.py:172)."""
    payload = {
        "ip": ip,
        "label": label,
        "score": score,
        "model_score": 0.0,
        "heuristic_label": label,
        "heuristic_confidence": 0.5,
        "heuristic_reason": "test",
        "request_count": 1,
        "duration": 0.0,
        "model_loaded": True,
        "reason": "test",
    }
    payload.update(overrides)
    return payload


class TestCounters:
    def test_counts_total_blocked_and_allowed(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(label="bot"))
        recorder.record(decision(label="human"))
        recorder.record(decision(label="human"))

        stats = recorder.stats()
        assert stats["total"] == 3
        assert stats["blocked"] == 1
        assert stats["allowed"] == 2

    def test_bot_rate_is_blocked_over_total(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(label="bot"))
        recorder.record(decision(label="human"))

        assert recorder.stats()["bot_rate"] == 0.5

    def test_empty_recorder_reports_zeroes_not_a_division_error(self):
        stats = InMemoryDecisionRecorder().stats()

        assert stats["total"] == 0
        assert stats["bot_rate"] == 0.0
        assert stats["avg_score"] == 0.0

    def test_avg_score_is_the_running_mean_of_blended_scores(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(score=0.2))
        recorder.record(decision(score=0.4))

        assert recorder.stats()["avg_score"] == pytest.approx(0.3)

    def test_avg_score_survives_the_ring_overflowing(self):
        # The mean is a running total, not an average of what is still in the
        # ring — otherwise stats would silently change as history scrolls off.
        recorder = InMemoryDecisionRecorder(capacity=2)

        for score in (0.0, 0.0, 1.0, 1.0):
            recorder.record(decision(score=score))

        assert recorder.stats()["avg_score"] == pytest.approx(0.5)


class TestHistogram:
    def test_scores_fall_into_twenty_buckets(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(score=0.0))
        recorder.record(decision(score=0.99))

        histogram = recorder.stats()["histogram"]
        assert len(histogram) == 20
        assert histogram[0] == 1
        assert histogram[19] == 1

    def test_a_score_of_one_lands_in_the_last_bucket_not_out_of_range(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(score=1.0))

        assert recorder.stats()["histogram"][19] == 1


class TestTopBlockedIps:
    def test_ranks_blocked_ips_by_count(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(ip="10.0.0.1", label="bot"))
        recorder.record(decision(ip="10.0.0.2", label="bot"))
        recorder.record(decision(ip="10.0.0.2", label="bot"))

        assert recorder.stats()["top_blocked_ips"][0] == {"ip": "10.0.0.2", "count": 2}

    def test_allowed_requests_do_not_appear(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(ip="10.0.0.9", label="human"))

        assert recorder.stats()["top_blocked_ips"] == []


class TestRecent:
    def test_returns_decisions_newest_first(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision(ip="first"))
        recorder.record(decision(ip="second"))

        assert [d["ip"] for d in recorder.recent()] == ["second", "first"]

    def test_stamps_each_decision_with_a_timestamp(self):
        recorder = InMemoryDecisionRecorder()

        recorder.record(decision())

        assert isinstance(recorder.recent()[0]["ts"], float)

    def test_drops_the_oldest_decision_past_capacity(self):
        recorder = InMemoryDecisionRecorder(capacity=2)

        for name in ("a", "b", "c"):
            recorder.record(decision(ip=name))

        assert [d["ip"] for d in recorder.recent()] == ["c", "b"]

    def test_limit_caps_how_many_come_back(self):
        recorder = InMemoryDecisionRecorder()

        for name in ("a", "b", "c"):
            recorder.record(decision(ip=name))

        assert len(recorder.recent(limit=2)) == 2

    def test_recorded_decisions_are_copied_not_aliased(self):
        recorder = InMemoryDecisionRecorder()
        payload = decision()

        recorder.record(payload)
        payload["label"] = "bot"

        assert recorder.recent()[0]["label"] == "human"


class TestBlockedIpBound:
    """The blocked-IP set must not grow with the number of distinct attackers.

    Every other structure here is bounded — the event ring is capped, session
    keys expire, the counters are fixed fields. This one tracked one entry per
    distinct blocked IP forever, which against a rotating botnet is a slow leak
    with no ceiling.
    """

    def test_the_set_stops_growing_at_the_cap(self):
        from microguard.events import MAX_TRACKED_IPS

        recorder = InMemoryDecisionRecorder()

        for i in range(MAX_TRACKED_IPS + 500):
            recorder.record(decision(ip=f"10.0.{i // 256}.{i % 256}", label="bot"))

        assert recorder.tracked_ip_count() <= MAX_TRACKED_IPS

    def test_the_busiest_ips_survive_eviction(self):
        """Trimming keeps the highest counts, because the only consumer asks
        who is hitting hardest — not who has ever been blocked."""
        from microguard.events import MAX_TRACKED_IPS

        recorder = InMemoryDecisionRecorder()
        for _ in range(50):
            recorder.record(decision(ip="203.0.113.9", label="bot"))

        for i in range(MAX_TRACKED_IPS + 500):
            recorder.record(decision(ip=f"10.0.{i // 256}.{i % 256}", label="bot"))

        top = recorder.stats()["top_blocked_ips"]
        assert top[0] == {"ip": "203.0.113.9", "count": 50}

    def test_counters_are_unaffected_by_trimming(self):
        """Totals are cumulative and must not shrink when the set is trimmed."""
        from microguard.events import MAX_TRACKED_IPS

        recorder = InMemoryDecisionRecorder()
        blocks = MAX_TRACKED_IPS + 200

        for i in range(blocks):
            recorder.record(decision(ip=f"10.0.{i // 256}.{i % 256}", label="bot"))

        assert recorder.stats()["blocked"] == blocks
