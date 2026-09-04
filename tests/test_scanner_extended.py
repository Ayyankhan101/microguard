"""Extended tests for the scanner module."""

import pytest
from microguard.scanner import (
    ProbeResult, extract_probe_features, _analyze_probes,
    format_probe_report, format_probe_verbose, _probe_features_to_vector,
)


class TestAnalyzeProbes:
    """Tests for _analyze_probes heuristic analysis."""

    def test_empty_results(self):
        score, reason = _analyze_probes([], {})
        assert 0.0 <= score <= 1.0
        assert 'no probe results' in reason

    def test_normal_200(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=200,
            headers={"Server": "nginx", "Content-Type": "text/html"},
            timing={"total": 0.5, "ttfb": 0.3},
        )
        score, reason = _analyze_probes([result], {})
        assert 0.0 <= score <= 0.5
        assert 'no strong' in reason

    def test_rate_limited_429(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=429,
            headers={"Server": "nginx", "Retry-After": "60"},
            timing={"total": 0.1},
        )
        score, reason = _analyze_probes([result], {})
        assert score >= 0.7
        assert 'rate limited' in reason

    def test_forbidden_403(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=403,
            headers={"Server": "cloudflare"},
            timing={"total": 0.05},
        )
        score, reason = _analyze_probes([result], {})
        assert score >= 0.6
        assert 'forbidden' in reason

    def test_server_error_503(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=503,
            headers={"Server": "nginx"},
            timing={"total": 0.5},  # Normal timing so slow rule doesn't fire first
        )
        score, reason = _analyze_probes([result], {})
        assert 'server error' in reason

    def test_very_fast_response(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=200,
            headers={"Server": "nginx"},
            timing={"total": 0.02},
        )
        score, reason = _analyze_probes([result], {})
        assert 'fast' in reason or 'cached' in reason

    def test_connection_error(self):
        result = ProbeResult(
            url="https://example.com",
            error="Connection refused",
            timing={"total": 0.001},
        )
        score, reason = _analyze_probes([result], {})
        assert 'connection error' in reason

    def test_well_protected_site(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=200,
            headers={
                "Server": "cloudflare",
                "Content-Security-Policy": "default-src 'self'",
                "X-Frame-Options": "DENY",
                "Strict-Transport-Security": "max-age=31536000",
                "X-Content-Type-Options": "nosniff",
            },
            timing={"total": 0.3, "ttfb": 0.2},
        )
        score, reason = _analyze_probes([result], {})
        assert score <= 0.3
        assert 'protected' in reason

    def test_consistent_timing_bot(self):
        """Multiple probes with very consistent timing = bot signal."""
        results = []
        for i in range(5):
            results.append(ProbeResult(
                url="https://example.com",
                status_code=200,
                headers={"Server": "nginx"},
                timing={"total": 0.15 + i * 0.001},  # Nearly identical
            ))
        features = {'timing_cv': 0.01}  # Very consistent
        score, reason = _analyze_probes(results, features)
        assert score >= 0.5
        assert 'consistent' in reason or 'timing' in reason


class TestFormatProbeVerbose:
    """Tests for format_probe_verbose function."""

    def test_basic_output(self):
        result = {
            'url': 'https://example.com',
            'status_code': 200,
            'probes': 3,
            'timing': {'total': 0.5, 'ttfb': 0.3},
            'combined_score': 0.23,
            'label': 'human',
            'threshold': 0.7,
            'heuristic_score': 0.3,
            'heuristic_reason': 'no strong signals',
            'model_score': 0.1,
            'features': {
                'response_time': 0.5,
                'ttfb': 0.3,
                'timing_cv': 0.15,
                'status_code': 0.2,
                'header_count': 0.5,
                'body_entropy': 4.5,
                'body_length': 0.01,
                'has_server_header': 1.0,
                'has_x_powered_by': 0.0,
                'has_content_security_policy': 0.0,
                'has_x_frame_options': 0.0,
                'has_strict_transport': 1.0,
                'has_accept_language': 0.0,
                'redirect_count': 0.0,
                'server_bot_score': 0.0,
                'has_rate_limit': 0.0,
            },
            'headers': {'Server': 'nginx'},
        }
        output = format_probe_verbose(result)
        assert 'example.com' in output
        assert 'Feature Vector' in output
        assert 'response_time' in output
        assert 'Analysis' in output

    def test_shows_timing_notes(self):
        result = {
            'url': 'https://example.com',
            'status_code': 200,
            'probes': 3,
            'timing': {'total': 0.5},
            'combined_score': 0.8,
            'label': 'bot',
            'threshold': 0.7,
            'heuristic_score': 0.7,
            'heuristic_reason': 'consistent timing',
            'model_score': 0.6,
            'features': {
                'response_time': 0.5,
                'ttfb': 0.3,
                'timing_cv': 0.02,
                'status_code': 0.2,
                'header_count': 0.5,
                'body_entropy': 4.5,
                'body_length': 0.01,
                'has_server_header': 1.0,
                'has_x_powered_by': 0.0,
                'has_content_security_policy': 0.0,
                'has_x_frame_options': 0.0,
                'has_strict_transport': 0.0,
                'has_accept_language': 0.0,
                'redirect_count': 0.0,
                'server_bot_score': 0.3,
                'has_rate_limit': 0.0,
            },
            'headers': {'Server': 'cloudflare'},
        }
        output = format_probe_verbose(result)
        assert 'bot signal' in output  # CV < 0.05


class TestProbeFeaturesVector:
    """Tests for _probe_features_to_vector."""

    def test_vector_length(self):
        features = {
            'response_time': 0.5,
            'ttfb': 0.3,
            'timing_cv': 0.15,
            'header_count': 0.5,
            'has_accept_language': 1.0,
            'body_entropy': 4.5,
            'body_length': 0.01,
            'status_code': 0.2,
        }
        vector = _probe_features_to_vector(features)
        assert len(vector) == 19

    def test_empty_features(self):
        vector = _probe_features_to_vector({})
        assert len(vector) == 19
        assert all(v == 0.0 for v in vector)


class TestExtractProbeFeatures:
    """Extended tests for extract_probe_features."""

    def test_cloudflare_headers(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=200,
            headers={
                "Server": "cloudflare",
                "CF-Ray": "abc123",
                "CF-Cache-Status": "HIT",
            },
            body=b"<html>Cloudflare</html>",
            timing={"total": 0.2, "ttfb": 0.1},
        )
        features = extract_probe_features([result])
        assert features['has_server_header'] == 1.0
        assert features['server_bot_score'] == 0.3  # Cloudflare detected

    def test_security_headers_all(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=200,
            headers={
                "Server": "nginx",
                "Content-Security-Policy": "default-src 'self'",
                "X-Frame-Options": "DENY",
                "Strict-Transport-Security": "max-age=31536000",
                "X-Content-Type-Options": "nosniff",
                "X-Powered-By": "Express",
            },
            body=b"<html>Secure</html>",
            timing={"total": 0.3, "ttfb": 0.1},
        )
        features = extract_probe_features([result])
        assert features['has_content_security_policy'] == 1.0
        assert features['has_x_frame_options'] == 1.0
        assert features['has_strict_transport'] == 1.0
        assert features['has_x_powered_by'] == 1.0
        assert features['has_server_header'] == 1.0
