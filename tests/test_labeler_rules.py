"""Tests for expanded heuristic labeler rules.

Tests Cloudflare WAF detection, API key patterns, and botnet signatures.
"""

import pytest
from datetime import datetime, timedelta
from microguard.parser import LogEntry
from microguard.features import Session
from microguard.labeler import (
    label_session, label_entries,
    CLOUDFLARE_BYPASS_RE, API_KEY_SCAN_RE, BOTNET_URL_RE,
    ATTACK_TOOL_RE, BRUTE_FORCE_ENDPOINTS,
    _check_cloudflare_signals, _check_api_key_patterns, _check_botnet_signatures,
)


def _make_entry(
    ip="192.168.1.1",
    timestamp=None,
    method="GET",
    url="/page",
    status=200,
    size=1024,
    referer="-",
    user_agent="Mozilla/5.0",
):
    if timestamp is None:
        timestamp = datetime(2023, 3, 24, 17, 0, 0)
    return LogEntry(
        ip=ip, timestamp=timestamp, method=method, url=url,
        status=status, size=size, referer=referer, user_agent=user_agent,
    )


def _make_session(ip, ua, entries):
    session = Session(ip, ua)
    for e in entries:
        session.add_request(e)
    return session


# ===== Cloudflare WAF Tests =====

class TestCloudflareWAF:
    """Tests for Cloudflare WAF detection."""

    def test_cloudflare_bypass_ua(self):
        session = _make_session(
            "10.0.0.1", "cf-worker/1.0",
            [_make_entry(ip="10.0.0.1", url="/api", user_agent="cf-worker/1.0")]
        )
        label, conf, reason = label_session(session)
        assert label == 'bot'
        assert conf >= 0.85
        assert 'Cloudflare' in reason

    def test_incapsula_ua(self):
        session = _make_session(
            "10.0.0.1", "Incapsula-Inspector",
            [_make_entry(ip="10.0.0.1", url="/api", user_agent="Incapsula-Inspector")]
        )
        label, conf, reason = label_session(session)
        assert label == 'bot'
        assert conf >= 0.85

    def test_cloudflare_protected_endpoint_no_referrer(self):
        entries = []
        for i in range(15):
            entries.append(_make_entry(
                ip="10.0.0.1",
                url=f"/wp-admin?page={i}",
                user_agent="SomeCustomTool/1.0",
                referer="-",
            ))
        session = _make_session("10.0.0.1", "SomeCustomTool/1.0", entries)
        label, conf, reason = label_session(session)
        # Should be caught by either WAF or scanner pattern
        assert label == 'bot'

    def test_cloudflare_regex_matches(self):
        assert CLOUDFLARE_BYPASS_RE.search("cf-worker/1.0")
        assert CLOUDFLARE_BYPASS_RE.search("cloudflare")
        assert CLOUDFLARE_BYPASS_RE.search("Incapsula-Inspector")
        assert not CLOUDFLARE_BYPASS_RE.search("Mozilla/5.0")


# ===== API Key Pattern Tests =====

class TestAPIKeyPatterns:
    """Tests for API key scanning detection."""

    def test_api_key_url_scan(self):
        entries = []
        for i in range(15):
            entries.append(_make_entry(
                ip="10.0.0.1",
                url=f"/api/v1?key=test{i}&token=x",
                user_agent="python-requests/2.28.0",
            ))
        session = _make_session("10.0.0.1", "python-requests/2.28.0", entries)
        label, conf, reason = label_session(session)
        assert label == 'bot'
        # Should be caught by either API key or bot UA
        assert conf >= 0.70

    def test_credential_brute_force(self):
        entries = []
        base_time = datetime(2023, 3, 24, 17, 0, 0)
        for i in range(15):
            entries.append(_make_entry(
                ip="10.0.0.1",
                timestamp=base_time + timedelta(seconds=i * 2),
                url="/wp-login.php",
                user_agent="Mozilla/5.0",
            ))
        session = _make_session("10.0.0.1", "Mozilla/5.0", entries)
        label, conf, reason = label_session(session)
        assert label == 'bot'

    def test_api_key_regex_matches(self):
        assert API_KEY_SCAN_RE.search("/api?key=test")
        assert API_KEY_SCAN_RE.search("/api?token=abc")
        assert API_KEY_SCAN_RE.search("/api?api_key=xyz")
        assert API_KEY_SCAN_RE.search("/oauth2/token")
        assert not API_KEY_SCAN_RE.search("/products")


# ===== Botnet Signature Tests =====

class TestBotnetSignatures:
    """Tests for botnet and attack tool detection."""

    def test_attack_tool_ua(self):
        session = _make_session(
            "10.0.0.1", "Nuclei - Open-source project",
            [_make_entry(ip="10.0.0.1", url="/vuln", user_agent="Nuclei - Open-source project")]
        )
        label, conf, reason = label_session(session)
        assert label == 'bot'
        assert conf >= 0.85

    def test_mirai_iot_scan(self):
        entries = []
        for i in range(20):
            entries.append(_make_entry(
                ip="185.220.101.1",
                url="/shell.cgi" if i % 3 == 0 else "/HNAP1" if i % 3 == 1 else "/omega.cgi",
                user_agent="Go-http-client/1.1",
            ))
        session = _make_session("185.220.101.1", "Go-http-client/1.1", entries)
        label, conf, reason = label_session(session)
        assert label == 'bot'

    def test_directory_brute_force(self):
        entries = []
        base_time = datetime(2023, 3, 24, 17, 0, 0)
        dirs = ["/admin", "/backup", "/config", "/debug", "/env", "/git",
                "/hidden", "/private", "/secret", "/test", "/tmp",
                "/wp-admin", "/phpmyadmin", "/.env", "/config.json"]
        for i in range(32):
            entries.append(_make_entry(
                ip="91.189.88.162",
                timestamp=base_time + timedelta(seconds=i * 0.1),
                url=dirs[i % len(dirs)],
                status=403 if i % 2 == 0 else 404,
                user_agent="Go-http-client/1.1",
            ))
        session = _make_session("91.189.88.162", "Go-http-client/1.1", entries)
        label, conf, reason = label_session(session)
        assert label == 'bot'

    def test_ua_rotation(self):
        entries = []
        base_time = datetime(2023, 3, 24, 17, 0, 0)
        for i in range(20):
            entries.append(_make_entry(
                ip="10.0.0.1",
                timestamp=base_time + timedelta(seconds=i),
                url=f"/page/{i}",
                user_agent=f"Bot{i}/1.0",
            ))
        session = _make_session("10.0.0.1", "Bot0/1.0", entries)
        label, conf, reason = label_session(session)
        assert label == 'bot'

    def test_botnet_regex_matches(self):
        assert BOTNET_URL_RE.search("/shell.cgi")
        assert BOTNET_URL_RE.search("/HNAP1")
        assert BOTNET_URL_RE.search("/omega.cgi")
        assert BOTNET_URL_RE.search("/boaform")
        assert not BOTNET_URL_RE.search("/products")

    def test_attack_tool_regex_matches(self):
        assert ATTACK_TOOL_RE.search("Nuclei - Open-source project")
        assert ATTACK_TOOL_RE.search("ffuf/2.0")
        assert ATTACK_TOOL_RE.search("Go net/http client")
        assert ATTACK_TOOL_RE.search("feroxbuster/2.0")
        assert not ATTACK_TOOL_RE.search("Mozilla/5.0 Chrome")


# ===== Human Signal Tests =====

class TestHumanSignals:
    """Tests for human traffic detection."""

    def test_known_browser_normal_session(self):
        entries = []
        base_time = datetime(2023, 3, 24, 17, 0, 0)
        for i in range(10):
            entries.append(_make_entry(
                ip="192.168.1.1",
                timestamp=base_time + timedelta(seconds=i * 3 + (i % 3)),
                url=f"/page/{i}",
                referer=f"https://example.com/page/{i-1}" if i > 0 else "https://example.com",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ))
        session = _make_session(
            "192.168.1.1",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            entries,
        )
        label, conf, reason = label_session(session)
        assert label == 'human'

    def test_variable_timing_human(self):
        entries = []
        base_time = datetime(2023, 3, 24, 17, 0, 0)
        # Very variable timing
        delays = [0.5, 5.0, 0.2, 10.0, 0.3, 8.0, 0.1, 3.0, 7.0, 0.4]
        for i, delay in enumerate(delays):
            entries.append(_make_entry(
                ip="192.168.1.1",
                timestamp=base_time + timedelta(seconds=sum(delays[:i])),
                url=f"/page/{i}",
                user_agent="Mozilla/5.0 Chrome/120.0.0.0",
            ))
        session = _make_session(
            "192.168.1.1",
            "Mozilla/5.0 Chrome/120.0.0.0",
            entries,
        )
        label, conf, reason = label_session(session)
        # Variable timing is a human signal
        assert label == 'human' or conf < 0.6


# ===== Edge Cases =====

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_session(self):
        session = Session("10.0.0.1", "")
        label, conf, reason = label_session(session)
        assert label == 'human'
        assert conf == 0.5

    def test_single_request(self):
        session = _make_session(
            "10.0.0.1", "python-requests/2.28.0",
            [_make_entry(ip="10.0.0.1", user_agent="python-requests/2.28.0")]
        )
        label, conf, reason = label_session(session)
        assert label == 'bot'

    def test_label_entries_batch(self):
        entries = []
        base_time = datetime(2023, 3, 24, 17, 0, 0)
        for i in range(20):
            entries.append(_make_entry(
                ip="192.168.1.1",
                timestamp=base_time + timedelta(seconds=i),
                url=f"/page/{i}",
                user_agent="Mozilla/5.0 Chrome/120.0.0.0",
            ))
        results = label_entries(entries)
        assert len(results) >= 1
        for session, label, conf, reason in results:
            assert label in ('bot', 'human')
            assert 0.0 <= conf <= 1.0
