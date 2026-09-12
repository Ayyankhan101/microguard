"""Tests for `microguard signals` and `microguard explain`.

The refresh loop takes a test-only `_max_iterations`, matching the seam
`watch_logfile` and `decision_stream` already use to terminate an otherwise
infinite loop. Product code never passes it.
"""

import json
from datetime import datetime, timezone

import pytest
import redis

from microguard.live.explain import explain_actor
from microguard.live.redis_store import RedisSessionStateStore
from microguard.live.signals_runner import build_sources, run_refresher
from microguard.parser import LogEntry

BASE = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)


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


def _entry(ip, url="/products", ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"):
    return LogEntry(ip=ip, timestamp=BASE, method="GET", url=url, status=200,
                    size=100, referer="", user_agent=ua)


class TestBuildSources:
    def test_sources_report_health_even_when_a_feed_fails(self, tmp_path):
        def boom():
            raise OSError("network down")

        tor, ranges, health = build_sources(cache=tmp_path, tor_fetch=boom, cidr_fetch=boom)

        assert tor == set()
        assert ranges == []
        by_name = {h.name: h for h in health}
        assert by_name["tor"].ok is False
        assert "network down" in by_name["tor"].error

    def test_a_working_feed_reports_its_entry_count(self, tmp_path):
        tor, _ranges, health = build_sources(
            cache=tmp_path, tor_fetch=lambda: "1.1.1.1\n2.2.2.2\n",
            cidr_fetch=lambda: json.dumps({"prefixes": []}),
        )
        by_name = {h.name: h for h in health}
        assert tor == {"1.1.1.1", "2.2.2.2"}
        assert by_name["tor"].ok is True
        assert by_name["tor"].entries == 2


class TestRefreshLoop:
    def test_it_runs_the_requested_number_of_passes(self, clean):
        RedisSessionStateStore(clean, default_ttl=60).record_request(
            "1.1.1.1", "ua", _entry("1.1.1.1")
        )
        run_refresher(
            client=clean, interval=0, _max_iterations=2,
            tor_fetch=lambda: "1.1.1.1\n",
            cidr_fetch=lambda: json.dumps({"prefixes": []}),
            cache=None,
        )
        assert json.loads(clean.get("mg:v1:signals:1.1.1.1"))["tor_exit"] is True

    def test_a_pass_that_raises_does_not_kill_the_loop(self, clean, caplog):
        """A refresher that exits on one bad pass stops resolving signals
        silently, and the check server would just read staler and staler
        records without anything saying why."""
        calls = []

        class Exploding:
            def __getattr__(self, name):
                calls.append(name)
                raise redis.ConnectionError("gone")

        run_refresher(
            client=Exploding(), interval=0, _max_iterations=2,
            tor_fetch=lambda: "", cidr_fetch=lambda: "{}", cache=None,
        )
        assert len(calls) >= 2
        assert "refresh pass failed" in caplog.text


class TestExplain:
    def test_it_reports_the_deciding_rule(self, clean):
        store = RedisSessionStateStore(clean, default_ttl=60)
        store.record_request("5.5.5.5", "curl/8.0", _entry("5.5.5.5", ua="curl/8.0"))

        out = explain_actor(clean, "5.5.5.5")

        assert "5.5.5.5" in out
        assert "bot" in out.lower()
        assert "curl" in out.lower()

    def test_it_shows_resolved_signals_and_their_promotion(self, clean):
        store = RedisSessionStateStore(clean, default_ttl=60)
        store.record_request("1.1.1.1", "ua", _entry("1.1.1.1"))
        clean.set("mg:v1:signals:1.1.1.1", json.dumps({"tor_exit": True}))

        out = explain_actor(clean, "1.1.1.1")

        assert "tor_exit" in out
        assert "observe-only" in out.lower()

    def test_it_says_so_when_nothing_was_resolved(self, clean):
        store = RedisSessionStateStore(clean, default_ttl=60)
        store.record_request("1.1.1.1", "ua", _entry("1.1.1.1"))

        assert "not resolved" in explain_actor(clean, "1.1.1.1").lower()

    def test_an_unknown_actor_says_so_rather_than_inventing_a_verdict(self, clean):
        out = explain_actor(clean, "203.0.113.99")
        assert "no live session" in out.lower()

    def test_it_does_not_record_a_request(self, clean):
        """Diagnosis must not change the thing being diagnosed."""
        store = RedisSessionStateStore(clean, default_ttl=60)
        store.record_request("1.1.1.1", "ua", _entry("1.1.1.1"))

        explain_actor(clean, "1.1.1.1")

        assert clean.llen("live:v2:1.1.1.1") == 1


class TestExplainDegradedInputs:
    def test_a_corrupt_signal_record_reads_as_unresolved(self, clean):
        RedisSessionStateStore(clean, default_ttl=60).record_request(
            "1.1.1.1", "ua", _entry("1.1.1.1")
        )
        clean.set("mg:v1:signals:1.1.1.1", "{not json")

        assert "not resolved" in explain_actor(clean, "1.1.1.1").lower()

    def test_promoting_a_source_reports_it_as_enforced(self, clean):
        RedisSessionStateStore(clean, default_ttl=60).record_request(
            "1.1.1.1", "ua", _entry("1.1.1.1")
        )
        clean.set("mg:v1:signals:1.1.1.1", json.dumps({"tor_exit": True}))

        out = explain_actor(clean, "1.1.1.1", promoted=frozenset({"tor"}))

        assert "enforced" in out
        assert "Tor exit node" in out


class TestHeartbeatReader:
    def test_it_returns_none_before_the_refresher_has_ever_run(self, clean):
        from microguard.live.signals_runner import heartbeat

        assert heartbeat(clean) is None

    def test_it_decodes_a_recorded_pass(self, clean):
        from microguard.live.signals_refresher import HEARTBEAT_KEY
        from microguard.live.signals_runner import heartbeat

        clean.set(HEARTBEAT_KEY, json.dumps({"ts": 1.0, "resolved": 3, "sources": []}))
        assert heartbeat(clean)["resolved"] == 3

    def test_a_corrupt_heartbeat_reads_as_never_ran(self, clean):
        from microguard.live.signals_refresher import HEARTBEAT_KEY
        from microguard.live.signals_runner import heartbeat

        clean.set(HEARTBEAT_KEY, "{not json")
        assert heartbeat(clean) is None


class TestRunnerEntryPoint:
    def test_main_prints_source_state_and_starts_the_loop(self, monkeypatch, capsys):
        """The banner is the only place an operator sees a dead feed before
        the dashboard does."""
        import microguard.live.signals_runner as runner

        monkeypatch.setattr(
            runner.redis.Redis, "from_url",
            lambda *a, **k: type("C", (), {"ping": lambda s: True})(),
        )
        monkeypatch.setattr(
            runner, "build_sources",
            lambda *a, **k: (
                {"1.1.1.1"}, [],
                [runner.SourceHealth("tor", True, 1, 0.0),
                 runner.SourceHealth("hosting", False, 0, 0.0, "network down")],
            ),
        )
        monkeypatch.setattr(runner, "run_refresher", lambda *a, **k: None)

        runner.main(interval=7)

        out = capsys.readouterr().out
        assert "every 7s" in out
        assert "tor: 1 entries" in out
        assert "hosting: FAILED (network down)" in out

    def test_ctrl_c_shuts_down_cleanly(self, monkeypatch, capsys):
        import microguard.live.signals_runner as runner

        monkeypatch.setattr(
            runner.redis.Redis, "from_url",
            lambda *a, **k: type("C", (), {"ping": lambda s: True})(),
        )
        monkeypatch.setattr(runner, "build_sources", lambda *a, **k: (set(), [], []))

        def interrupt(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(runner, "run_refresher", interrupt)
        runner.main()

        assert "Shutting down" in capsys.readouterr().out
