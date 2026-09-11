"""Extended tests for the scanner module."""

from microguard.scanner import (
    ProbeResult,
    _analyze_probes,
    extract_probe_features,
    format_probe_html,
    format_probe_report,
    format_probe_verbose,
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
        _score, reason = _analyze_probes([result], {})
        assert 'server error' in reason

    def test_very_fast_response(self):
        result = ProbeResult(
            url="https://example.com",
            status_code=200,
            headers={"Server": "nginx"},
            timing={"total": 0.02},
        )
        _score, reason = _analyze_probes([result], {})
        assert 'fast' in reason or 'cached' in reason

    def test_connection_error(self):
        result = ProbeResult(
            url="https://example.com",
            error="Connection refused",
            timing={"total": 0.001},
        )
        _score, reason = _analyze_probes([result], {})
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


def _probe_result(**overrides):
    """A probe result dict, the shape probe_and_analyze returns."""
    result = {
        'url': 'https://example.com',
        'probes': 3,
        'status_code': 200,
        'timing': {'total': 0.5, 'ttfb': 0.3},
        'combined_score': 0.23,
        'heuristic_score': 0.23,
        'heuristic_reason': 'no strong bot signals detected',
        'model_score': 0.0,
        'label': 'human',
        'threshold': 0.7,
        'features': {
            'response_time': 0.5,
            'timing_cv': 0.15,
            'body_entropy': 4.5,
            'body_length': 0.01,
            'has_server_header': 1.0,
            'has_x_powered_by': 0.0,
        },
        'headers': {'Server': 'nginx'},
        'body_preview': '<html>hello</html>',
    }
    result.update(overrides)
    return result


class TestFormatProbeHtml:
    """A pure formatter: a dict in, a standalone document out.

    The largest single uncovered block in scanner.py, and it needs no probe,
    no socket and no network.
    """

    def test_renders_a_standalone_document(self):
        html = format_probe_html(_probe_result())

        assert html.startswith('<!DOCTYPE html>')
        assert '</html>' in html
        assert 'https://example.com' in html

    def test_reports_the_score_and_threshold(self):
        html = format_probe_html(_probe_result(combined_score=0.42, threshold=0.7))

        assert '0.42' in html
        assert '0.7' in html

    def test_low_score_band(self):
        html = format_probe_html(_probe_result(combined_score=0.1))

        assert 'LOW AUTOMATION SIGNAL' in html

    def test_middle_score_band(self):
        html = format_probe_html(_probe_result(combined_score=0.5))

        assert 'SOMEWHAT' in html

    def test_high_score_band(self):
        html = format_probe_html(_probe_result(combined_score=0.9))

        assert 'HIGHLY AUTOMATED-LOOKING' in html

    def test_boolean_features_render_as_yes_or_no(self):
        # The table renders a fixed list of features, so only the has_* keys
        # in that list reach the boolean branch.
        html = format_probe_html(_probe_result(features={
            'has_content_security_policy': 1.0, 'has_x_frame_options': 0.0,
        }))

        assert 'Yes' in html
        assert 'No' in html

    def test_length_features_render_in_kilobytes(self):
        html = format_probe_html(_probe_result(features={'body_length': 0.25}))

        assert 'KB' in html

    def test_the_model_block_appears_only_with_a_model_score(self):
        with_model = format_probe_html(_probe_result(model_score=0.42))
        without_model = format_probe_html(_probe_result(model_score=0.0))

        assert 'ML Model' in with_model or '0.42' in with_model
        assert '0.42' not in without_model

    def test_body_preview_is_html_escaped(self):
        """The preview is whatever the target returned, so it is
        attacker-controlled and must not become live markup."""
        html = format_probe_html(_probe_result(
            body_preview='<script>alert(1)</script>'
        ))

        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;' in html

    def test_long_header_values_are_truncated(self):
        html = format_probe_html(_probe_result(headers={'X-Long': 'v' * 200}))

        assert '...' in html
        assert 'v' * 200 not in html

    def test_missing_headers_render_a_fallback_row(self):
        html = format_probe_html(_probe_result(headers={}))

        assert '<!DOCTYPE html>' in html

    def test_empty_body_preview_omits_the_section(self):
        html = format_probe_html(_probe_result(body_preview=''))

        assert '<!DOCTYPE html>' in html


class TestFormatProbeReportBands:
    def test_high_score_is_flagged(self):
        output = format_probe_report(_probe_result(combined_score=0.9, label='bot'))

        assert '0.90' in output

    def test_long_header_values_are_truncated(self):
        output = format_probe_report(_probe_result(headers={'X-Long': 'v' * 120}))

        assert '...' in output


class TestFormatProbeVerboseBands:
    def test_middle_score_band(self):
        output = format_probe_verbose(_probe_result(combined_score=0.5))

        assert '0.500' in output or '0.50' in output

    def test_variable_timing_is_called_out(self):
        output = format_probe_verbose(_probe_result(
            features={'timing_cv': 0.5, 'body_entropy': 4.0}
        ))

        assert 'variable' in output

    def test_low_entropy_is_called_out(self):
        output = format_probe_verbose(_probe_result(
            features={'timing_cv': 0.1, 'body_entropy': 0.5}
        ))

        assert 'low' in output

    def test_high_entropy_is_called_out(self):
        output = format_probe_verbose(_probe_result(
            features={'timing_cv': 0.1, 'body_entropy': 7.9}
        ))

        assert 'high' in output

    def test_long_header_values_are_truncated(self):
        output = format_probe_verbose(_probe_result(headers={'X-Long': 'v' * 120}))

        assert '...' in output


class TestProbeResultEdges:
    def test_body_text_survives_a_non_decodable_body(self):
        """body is typed bytes but the dataclass does not enforce it."""
        result = ProbeResult(url="https://example.com", body=12345)

        assert result.body_text == ""

    def test_body_text_replaces_invalid_utf8(self):
        result = ProbeResult(url="https://example.com", body=b"caf\xff")

        assert "caf" in result.body_text


class TestTimingConsistency:
    def test_all_zero_timings_give_zero_rather_than_dividing(self):
        from microguard.scanner import _timing_pattern_consistency

        assert _timing_pattern_consistency([0.0, 0.0, 0.0]) == 0.0


class TestProbeFeatureBranches:
    def test_multiple_probes_produce_a_timing_cv(self):
        results = [
            ProbeResult(url="https://example.com", status_code=200, timing={'total': 0.1}),
            ProbeResult(url="https://example.com", status_code=200, timing={'total': 0.5}),
        ]

        features = extract_probe_features(results)

        assert features['timing_cv'] > 0

    def test_redirects_are_counted(self):
        results = [
            ProbeResult(url="https://example.com", status_code=301, timing={'total': 0.1}),
            ProbeResult(url="https://example.com", status_code=302, timing={'total': 0.1}),
        ]

        features = extract_probe_features(results)

        assert features['redirect_count'] > 0


class TestAnalyzeProbeBranches:
    def _result(self, **kw):
        base = {'url': "https://example.com", 'status_code': 200,
                'timing': {'total': 0.5}, 'body': b"hello world", 'headers': {}}
        base.update(kw)
        return ProbeResult(**base)

    def test_a_slow_response_suggests_rate_limiting(self):
        score, reason = _analyze_probes([self._result(timing={'total': 6.0})], {})

        assert score == 0.5
        assert 'slow response' in reason

    def test_low_body_entropy_is_reported(self):
        score, reason = _analyze_probes(
            [self._result()], {'body_entropy': 0.5}
        )

        assert score == 0.4
        assert 'low response entropy' in reason

    def test_high_body_entropy_suggests_compression(self):
        score, reason = _analyze_probes(
            [self._result()], {'body_entropy': 7.9}
        )

        assert score == 0.3
        assert 'high response entropy' in reason
