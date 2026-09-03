"""Tests for microguard.scanner module."""

import pytest
from microguard.scanner import (
    probe_url,
    probe_url_multiple,
    extract_probe_features,
    format_probe_report,
    _shannon_entropy,
    _timing_pattern_consistency,
)


class TestShannonEntropy:
    """Tests for Shannon entropy calculation."""
    
    def test_empty_string(self):
        assert _shannon_entropy("") == 0.0
    
    def test_single_char(self):
        assert _shannon_entropy("a") == 0.0
    
    def test_uniform_string(self):
        assert _shannon_entropy("aaaa") == 0.0
    
    def test_diverse_string(self):
        entropy = _shannon_entropy("abcdefghij")
        assert entropy > 0
    
    def test_binary_data(self):
        entropy = _shannon_entropy(b"\x00\x01\x02\x03\x04\x05\x06\x07")
        assert entropy > 0


class TestTimingPattern:
    """Tests for timing pattern consistency."""
    
    def test_empty_list(self):
        assert _timing_pattern_consistency([]) == 0.0
    
    def test_single_value(self):
        assert _timing_pattern_consistency([1.0]) == 0.0
    
    def test_uniform_timing(self):
        # All same value = low CV = bot-like
        timings = [1.0, 1.0, 1.0, 1.0, 1.0]
        cv = _timing_pattern_consistency(timings)
        assert cv == pytest.approx(0.0, abs=0.01)
    
    def test_variable_timing(self):
        # Very different values = high CV = human-like
        timings = [0.1, 5.0, 0.2, 10.0, 0.3]
        cv = _timing_pattern_consistency(timings)
        assert cv > 1.0


class TestProbeFeatures:
    """Tests for feature extraction from probe results."""
    
    def test_empty_results(self):
        features = extract_probe_features([])
        assert features == {}
    
    def test_basic_features(self):
        """Test that basic features are extracted."""
        from microguard.scanner import ProbeResult
        
        result = ProbeResult(
            url="http://example.com",
            status_code=200,
            headers={"Server": "nginx", "Content-Type": "text/html"},
            body=b"Hello World",
            timing={"total": 0.5, "ttfb": 0.1, "transfer": 0.4},
        )
        
        features = extract_probe_features([result])
        
        assert features['response_time'] == 0.5
        assert features['ttfb'] == 0.1
        assert features['status_code'] == 200 / 1000
        assert features['header_count'] > 0
        assert features['body_entropy'] > 0
        assert features['has_server_header'] == 1.0
        assert features['has_x_powered_by'] == 0.0
    
    def test_security_headers(self):
        """Test detection of security headers."""
        from microguard.scanner import ProbeResult
        
        result = ProbeResult(
            url="http://example.com",
            status_code=200,
            headers={
                "Server": "nginx",
                "Content-Security-Policy": "default-src 'self'",
                "X-Frame-Options": "DENY",
                "Strict-Transport-Security": "max-age=31536000",
            },
            body=b"<html>Secure Site</html>",
            timing={"total": 0.2, "ttfb": 0.05, "transfer": 0.15},
        )
        
        features = extract_probe_features([result])
        
        assert features['has_content_security_policy'] == 1.0
        assert features['has_x_frame_options'] == 1.0
        assert features['has_strict_transport'] == 1.0


class TestProbeUrl:
    """Tests for URL probing (integration tests)."""
    
    @pytest.mark.skip(reason="Requires network access")
    def test_probe_valid_url(self):
        """Test probing a real URL."""
        result = probe_url("https://httpbin.org/get", timeout=5.0)
        assert result.status_code == 200
        assert result.timing.get('total', 0) > 0
    
    def test_probe_invalid_url(self):
        """Test probing an invalid URL."""
        result = probe_url("https://this-domain-does-not-exist-12345.invalid", timeout=2.0)
        assert result.error is not None or result.status_code >= 400
    
    def test_probe_bad_ssl(self):
        """Test probing with SSL verification disabled."""
        result = probe_url(
            "https://self-signed.badssl.com/",
            verify_ssl=False,
            timeout=5.0,
        )
        # Should not raise, might get a response or error
        assert result.timing.get('total', 0) >= 0


class TestFormatProbeReport:
    """Tests for terminal report formatting."""
    
    def test_format_basic_report(self):
        """Test formatting a basic probe report."""
        result = {
            'url': 'https://example.com',
            'status_code': 200,
            'probes': 3,
            'timing': {'total': 0.25},
            'combined_score': 0.35,
            'label': 'human',
            'threshold': 0.7,
            'heuristic_score': 0.3,
            'heuristic_reason': 'no strong bot signals detected',
            'model_score': 0.4,
            'features': {
                'response_time': 0.25,
                'ttfb': 0.05,
                'body_entropy': 4.5,
                'body_length': 0.01,
                'timing_cv': 0.15,
                'has_content_security_policy': 1.0,
                'has_x_frame_options': 1.0,
                'has_strict_transport': 1.0,
            },
            'headers': {'Server': 'nginx'},
            'body_preview': '<html>Hello</html>',
        }
        
        report = format_probe_report(result)
        
        assert 'example.com' in report
        assert 'HTTP 200' in report
        assert '0.35' in report
        assert 'HUMAN' in report  # Label is uppercase in output
        assert 'Analysis:' in report
        assert 'Key Features:' in report
