"""Tests for the slow tier — the process that resolves signals out of band.

Nothing here touches the network. The fetcher is injected, because the point
of these tests is the cache-and-degrade behavior around a fetch, not the fetch
itself: a feed that fails must leave the last good answer in place rather than
blanking a signal the check server is about to read.
"""

import json
import time
from datetime import datetime, timezone

import pytest
import redis

from microguard.live.redis_store import RedisSessionStateStore
from microguard.live.signals_refresher import (
    HEARTBEAT_KEY,
    SignalRefresher,
    SourceHealth,
    load_cidr_ranges,
    load_tor_exit_nodes,
)
from microguard.parser import LogEntry

BASE = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def redis_client():
    try:
        client = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        client.ping()
        yield client
        client.flushdb()
        client.close()
    except redis.ConnectionError:
        pytest.skip("Redis not available on localhost:6379")


@pytest.fixture()
def clean_redis(redis_client):
    redis_client.flushdb()
    return redis_client


def _entry(ip):
    return LogEntry(
        ip=ip, timestamp=BASE, method="GET", url="/", status=200, size=1,
        referer="", user_agent="Mozilla/5.0",
    )


class TestTorExitNodes:
    def test_a_successful_fetch_is_returned_and_cached(self, tmp_path):
        cache = tmp_path / "tor.txt"
        nodes = load_tor_exit_nodes(cache, fetch=lambda: "1.1.1.1\n2.2.2.2\n")
        assert nodes == {"1.1.1.1", "2.2.2.2"}
        assert cache.exists()

    def test_a_failed_fetch_falls_back_to_the_cache(self, tmp_path):
        """However stale. A threat-intel signal must never break scoring, and
        yesterday's exit-node list is far better than none."""
        cache = tmp_path / "tor.txt"
        cache.write_text("9.9.9.9\n", encoding="utf-8")

        def boom():
            raise OSError("network down")

        assert load_tor_exit_nodes(cache, fetch=boom) == {"9.9.9.9"}

    def test_a_failed_fetch_with_no_cache_returns_empty(self, tmp_path):
        def boom():
            raise OSError("network down")

        assert load_tor_exit_nodes(tmp_path / "absent.txt", fetch=boom) == set()

    def test_a_fresh_cache_is_not_refetched(self, tmp_path):
        cache = tmp_path / "tor.txt"
        cache.write_text("9.9.9.9\n", encoding="utf-8")
        calls = []

        load_tor_exit_nodes(cache, max_age_hours=24, fetch=lambda: calls.append(1) or "")
        assert calls == []

    def test_a_stale_cache_is_refetched(self, tmp_path):
        cache = tmp_path / "tor.txt"
        cache.write_text("9.9.9.9\n", encoding="utf-8")
        import os
        old = time.time() - 60 * 60 * 48
        os.utime(cache, (old, old))

        assert load_tor_exit_nodes(cache, max_age_hours=24, fetch=lambda: "1.1.1.1\n") == {"1.1.1.1"}

    def test_blank_lines_and_comments_are_dropped(self, tmp_path):
        body = "1.1.1.1\n\n# a comment\n  2.2.2.2  \n"
        assert load_tor_exit_nodes(tmp_path / "t.txt", fetch=lambda: body) == {"1.1.1.1", "2.2.2.2"}


class TestCidrRanges:
    def test_aws_prefix_documents_are_parsed(self, tmp_path):
        body = json.dumps({"prefixes": [{"ip_prefix": "13.248.0.0/20"}],
                           "ipv6_prefixes": [{"ipv6_prefix": "2600:1f00::/24"}]})
        ranges = load_cidr_ranges(tmp_path / "aws.json", fetch=lambda: body)
        assert any(str(n) == "13.248.0.0/20" for n in ranges)

    def test_a_malformed_prefix_is_skipped_not_fatal(self, tmp_path):
        """One bad entry in a third-party document must not discard the rest."""
        body = json.dumps({"prefixes": [{"ip_prefix": "not-a-cidr"},
                                        {"ip_prefix": "13.248.0.0/20"}]})
        ranges = load_cidr_ranges(tmp_path / "aws.json", fetch=lambda: body)
        assert len(ranges) == 1

    def test_a_failed_fetch_falls_back_to_the_cache(self, tmp_path):
        cache = tmp_path / "aws.json"
        cache.write_text(json.dumps({"prefixes": [{"ip_prefix": "13.248.0.0/20"}]}), encoding="utf-8")

        def boom():
            raise OSError("network down")

        assert len(load_cidr_ranges(cache, fetch=boom)) == 1

    def test_a_failed_fetch_with_no_cache_returns_empty(self, tmp_path):
        def boom():
            raise OSError("network down")

        assert load_cidr_ranges(tmp_path / "absent.json", fetch=boom) == []


class TestRefreshPass:
    def test_it_writes_signals_only_for_actors_it_has_seen(self, clean_redis):
        store = RedisSessionStateStore(clean_redis, default_ttl=60)
        store.record_request("1.1.1.1", "Mozilla/5.0", _entry("1.1.1.1"))

        refresher = SignalRefresher(clean_redis, tor_nodes={"1.1.1.1"}, hosting_ranges=[])
        refresher.run_once()

        assert json.loads(clean_redis.get("mg:v1:signals:1.1.1.1"))["tor_exit"] is True
        assert clean_redis.get("mg:v1:signals:2.2.2.2") is None

    def test_an_actor_with_no_hits_still_gets_a_resolved_record(self, clean_redis):
        """'We looked and found nothing' is different evidence from 'we never
        looked', and only a written record can say the first one."""
        store = RedisSessionStateStore(clean_redis, default_ttl=60)
        store.record_request("8.8.8.8", "Mozilla/5.0", _entry("8.8.8.8"))

        SignalRefresher(clean_redis, tor_nodes=set(), hosting_ranges=[]).run_once()

        payload = json.loads(clean_redis.get("mg:v1:signals:8.8.8.8"))
        assert payload["tor_exit"] is False

    def test_records_expire_so_they_cannot_outlive_the_session(self, clean_redis):
        store = RedisSessionStateStore(clean_redis, default_ttl=60)
        store.record_request("1.1.1.1", "Mozilla/5.0", _entry("1.1.1.1"))

        SignalRefresher(clean_redis, tor_nodes=set(), hosting_ranges=[], ttl=30).run_once()

        assert 0 < clean_redis.ttl("mg:v1:signals:1.1.1.1") <= 30

    def test_a_hosting_range_hit_is_recorded(self, clean_redis):
        import ipaddress

        store = RedisSessionStateStore(clean_redis, default_ttl=60)
        store.record_request("13.248.0.5", "Mozilla/5.0", _entry("13.248.0.5"))

        SignalRefresher(
            clean_redis, tor_nodes=set(),
            hosting_ranges=[ipaddress.ip_network("13.248.0.0/20")],
        ).run_once()

        assert json.loads(clean_redis.get("mg:v1:signals:13.248.0.5"))["hosting_range"] is True

    def test_an_unparseable_actor_key_is_skipped(self, clean_redis):
        """Session keys are IP-derived, but a stray key in the namespace must
        not stop the pass for every other actor."""
        clean_redis.rpush("live:v2:not-an-ip", "{}")
        store = RedisSessionStateStore(clean_redis, default_ttl=60)
        store.record_request("1.1.1.1", "Mozilla/5.0", _entry("1.1.1.1"))

        resolved = SignalRefresher(clean_redis, tor_nodes={"1.1.1.1"}, hosting_ranges=[]).run_once()

        assert resolved == 1


class TestHeartbeat:
    def test_a_pass_stamps_the_heartbeat(self, clean_redis):
        SignalRefresher(clean_redis, tor_nodes=set(), hosting_ranges=[]).run_once()

        beat = json.loads(clean_redis.get(HEARTBEAT_KEY))
        assert beat["ts"] > 0
        assert beat["resolved"] == 0

    def test_the_heartbeat_carries_per_source_health(self, clean_redis):
        refresher = SignalRefresher(
            clean_redis,
            tor_nodes={"1.1.1.1"},
            hosting_ranges=[],
            health=[SourceHealth(name="tor", ok=True, entries=1, fetched_at=123.0),
                    SourceHealth(name="hosting", ok=False, entries=0, fetched_at=0.0,
                                 error="network down")],
        )
        refresher.run_once()

        sources = {s["name"]: s for s in json.loads(clean_redis.get(HEARTBEAT_KEY))["sources"]}
        assert sources["tor"]["ok"] is True
        assert sources["hosting"]["error"] == "network down"

    def test_the_heartbeat_does_not_expire(self, clean_redis):
        """A missing heartbeat means 'never ran'. If it expired on its own, a
        refresher that died an hour ago would be indistinguishable from one
        that was never started -- and the dashboard reads exactly that
        difference."""
        SignalRefresher(clean_redis, tor_nodes=set(), hosting_ranges=[]).run_once()
        assert clean_redis.ttl(HEARTBEAT_KEY) == -1


class TestCacheLocation:
    def test_it_honors_xdg_cache_home(self, monkeypatch, tmp_path):
        from microguard.live.signals_refresher import cache_dir

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert cache_dir() == tmp_path / "microguard"

    def test_it_falls_back_to_a_home_cache(self, monkeypatch, tmp_path):
        """Never inside the package: `data/` ships in the wheel and is
        read-only on a normal install."""
        from microguard.live.signals_refresher import cache_dir

        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert cache_dir() == tmp_path / ".cache" / "microguard"


class TestDegradedCaches:
    def test_an_unwritable_cache_does_not_discard_the_fetched_value(self, tmp_path, caplog):
        """A disk problem must cost the cache, not the signal that is already
        in hand."""
        blocked = tmp_path / "file" / "tor.txt"
        blocked.parent.write_text("not a directory", encoding="utf-8")

        assert load_tor_exit_nodes(blocked, fetch=lambda: "1.1.1.1\n") == {"1.1.1.1"}
        assert "could not write feed cache" in caplog.text

    def test_a_prefix_document_that_is_not_json_returns_no_ranges(self, tmp_path, caplog):
        assert load_cidr_ranges(tmp_path / "aws.json", fetch=lambda: "<html>") == []
        assert "not JSON" in caplog.text


class TestStaleCacheFallback:
    def test_a_stale_cache_survives_a_failed_refetch(self, tmp_path, caplog):
        """The case that matters operationally: the cache is old enough to
        refetch, the feed is down, and yesterday's list is still far better
        than none."""
        import os

        cache = tmp_path / "tor.txt"
        cache.write_text("9.9.9.9\n", encoding="utf-8")
        old = time.time() - 60 * 60 * 48
        os.utime(cache, (old, old))

        def boom():
            raise OSError("network down")

        assert load_tor_exit_nodes(cache, max_age_hours=24, fetch=boom) == {"9.9.9.9"}
        assert "using stale cache" in caplog.text


class TestAbuseScoreResolution:
    def test_a_configured_client_contributes_its_score(self, clean_redis):
        from microguard.live.redis_store import RedisSessionStateStore

        RedisSessionStateStore(clean_redis, default_ttl=60).record_request(
            "1.1.1.1", "ua", _entry("1.1.1.1")
        )

        class Lookup:
            def check_ip(self, ip):
                return 91.0

        SignalRefresher(clean_redis, tor_nodes=set(), hosting_ranges=[],
                        abuse_client=Lookup()).run_once()

        assert json.loads(clean_redis.get("mg:v1:signals:1.1.1.1"))["abuse_score"] == 91.0

    def test_a_none_score_leaves_the_field_absent(self, clean_redis):
        """None means 'no opinion' -- unconfigured, out of budget, or a failed
        lookup. Writing it as a number would let a failure read as a verdict."""
        from microguard.live.redis_store import RedisSessionStateStore

        RedisSessionStateStore(clean_redis, default_ttl=60).record_request(
            "1.1.1.1", "ua", _entry("1.1.1.1")
        )

        class Silent:
            def check_ip(self, ip):
                return None

        SignalRefresher(clean_redis, tor_nodes=set(), hosting_ranges=[],
                        abuse_client=Silent()).run_once()

        assert "abuse_score" not in json.loads(clean_redis.get("mg:v1:signals:1.1.1.1"))
