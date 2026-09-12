"""Tests for the one keyed threat-intel source.

No network. The HTTP call is injected, because what matters here is the
behavior around it: an unconfigured client must be inert, a cached answer must
not spend quota, and every failure must degrade to "no opinion" rather than to
a wrong one.
"""

import json
import time

from microguard.live.abuseipdb import FREE_TIER_DAILY_LIMIT, AbuseIPDBClient


class TestUnconfigured:
    def test_no_key_means_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABUSEIPDB_API_KEY", raising=False)
        assert AbuseIPDBClient(cache_path=tmp_path / "c.json").is_configured() is False

    def test_an_unconfigured_client_returns_none_and_never_calls_out(self, tmp_path, monkeypatch):
        """A missing key is the default state for most installs. It must be
        silent and free, not an error and not a request."""
        monkeypatch.delenv("ABUSEIPDB_API_KEY", raising=False)
        calls = []
        client = AbuseIPDBClient(
            cache_path=tmp_path / "c.json", fetch=lambda ip, key: calls.append(ip)
        )
        assert client.check_ip("1.2.3.4") is None
        assert calls == []

    def test_an_empty_key_counts_as_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABUSEIPDB_API_KEY", "   ")
        assert AbuseIPDBClient(cache_path=tmp_path / "c.json").is_configured() is False


class TestLookups:
    def _client(self, tmp_path, fetch, **kwargs):
        return AbuseIPDBClient(api_key="k", cache_path=tmp_path / "c.json", fetch=fetch, **kwargs)

    def test_a_score_is_returned_and_cached(self, tmp_path):
        calls = []

        def fetch(ip, key):
            calls.append(ip)
            return 91.0

        client = self._client(tmp_path, fetch)
        assert client.check_ip("1.2.3.4") == 91.0
        assert client.check_ip("1.2.3.4") == 91.0
        assert calls == ["1.2.3.4"], "a cached answer must not spend quota"

    def test_the_cache_survives_a_restart(self, tmp_path):
        self._client(tmp_path, lambda ip, key: 91.0).check_ip("1.2.3.4")

        calls = []
        fresh = self._client(tmp_path, lambda ip, key: calls.append(ip) or 0.0)
        assert fresh.check_ip("1.2.3.4") == 91.0
        assert calls == []

    def test_an_expired_entry_is_refetched(self, tmp_path):
        cache = tmp_path / "c.json"
        cache.write_text(
            json.dumps({"1.2.3.4": {"score": 10.0, "ts": time.time() - 90000}}),
            encoding="utf-8",
        )
        client = self._client(tmp_path, lambda ip, key: 91.0, ttl=86400)
        assert client.check_ip("1.2.3.4") == 91.0

    def test_a_failed_lookup_returns_none_rather_than_zero(self, tmp_path):
        """Zero is a real score meaning 'reported clean'. A failure must not
        be recorded as one, or an unreachable API would quietly vouch for
        every address it could not check."""
        def boom(ip, key):
            raise OSError("api down")

        assert self._client(tmp_path, boom).check_ip("1.2.3.4") is None

    def test_a_failure_is_not_cached(self, tmp_path):
        state = {"fail": True}

        def flaky(ip, key):
            if state["fail"]:
                raise OSError("api down")
            return 77.0

        client = self._client(tmp_path, flaky)
        assert client.check_ip("1.2.3.4") is None
        state["fail"] = False
        assert client.check_ip("1.2.3.4") == 77.0

    def test_a_corrupt_cache_file_is_not_fatal(self, tmp_path):
        (tmp_path / "c.json").write_text("{not json", encoding="utf-8")
        assert self._client(tmp_path, lambda ip, key: 55.0).check_ip("1.2.3.4") == 55.0


class TestQuota:
    def test_lookups_count_against_the_daily_budget(self, tmp_path):
        client = AbuseIPDBClient(api_key="k", cache_path=tmp_path / "c.json",
                                 fetch=lambda ip, key: 1.0)
        client.check_ip("1.1.1.1")
        client.check_ip("2.2.2.2")
        assert client.quota_used == 2
        assert client.quota_remaining == FREE_TIER_DAILY_LIMIT - 2

    def test_a_cached_answer_does_not_spend_quota(self, tmp_path):
        client = AbuseIPDBClient(api_key="k", cache_path=tmp_path / "c.json",
                                 fetch=lambda ip, key: 1.0)
        client.check_ip("1.1.1.1")
        client.check_ip("1.1.1.1")
        assert client.quota_used == 1

    def test_an_exhausted_budget_stops_calling_out(self, tmp_path):
        """The free tier is 1000 checks a day. Blowing through it gets the key
        rate-limited, which takes the signal down for everyone rather than for
        one lookup."""
        calls = []
        client = AbuseIPDBClient(
            api_key="k", cache_path=tmp_path / "c.json",
            fetch=lambda ip, key: calls.append(ip) or 1.0, daily_limit=2,
        )
        for i in range(5):
            client.check_ip(f"1.1.1.{i}")

        assert len(calls) == 2
        assert client.quota_remaining == 0

    def test_the_budget_resets_on_a_new_day(self, tmp_path):
        client = AbuseIPDBClient(api_key="k", cache_path=tmp_path / "c.json",
                                 fetch=lambda ip, key: 1.0, daily_limit=1)
        client.check_ip("1.1.1.1")
        assert client.quota_remaining == 0

        client._quota_day = "1999-01-01"
        client.check_ip("2.2.2.2")
        assert client.quota_used == 1


class TestDegradedCache:
    def test_an_unwritable_cache_does_not_lose_the_score(self, tmp_path, caplog):
        """A disk problem costs the cache, not the answer already in hand."""
        blocked = tmp_path / "file" / "c.json"
        blocked.parent.write_text("not a directory", encoding="utf-8")

        client = AbuseIPDBClient(api_key="k", cache_path=blocked, fetch=lambda ip, key: 88.0)

        assert client.check_ip("1.2.3.4") == 88.0
        assert "could not write abuseipdb cache" in caplog.text

    def test_a_cache_holding_the_wrong_json_type_is_ignored(self, tmp_path):
        (tmp_path / "c.json").write_text("[1, 2, 3]", encoding="utf-8")
        client = AbuseIPDBClient(api_key="k", cache_path=tmp_path / "c.json",
                                 fetch=lambda ip, key: 42.0)
        assert client.check_ip("1.2.3.4") == 42.0

    def test_no_cache_path_still_works_in_memory(self, tmp_path):
        client = AbuseIPDBClient(api_key="k", cache_path=None, fetch=lambda ip, key: 42.0)
        assert client.check_ip("1.2.3.4") == 42.0
        assert client.check_ip("1.2.3.4") == 42.0
        assert client.quota_used == 1
