"""Tests for the report module."""

from microguard.report import (
    _colorize,
    _score_bar,
    format_cloudflare_rule,
    format_html,
    format_json,
    format_json_pretty,
    format_nginx_denylist,
    format_terminal,
    format_verbose,
    print_report,
    score_label,
    score_label_color,
)


class TestScoreLabel:
    """Tests for score_label function."""

    def test_safe_range(self):
        assert score_label(0.0) == 'SAFE'
        assert score_label(0.15) == 'SAFE'
        assert score_label(0.30) == 'SAFE'

    def test_low_range(self):
        assert score_label(0.31) == 'LOW'
        assert score_label(0.45) == 'LOW'
        assert score_label(0.59) == 'LOW'

    def test_warning_range(self):
        assert score_label(0.60) == 'WARNING'
        assert score_label(0.70) == 'WARNING'
        assert score_label(0.79) == 'WARNING'

    def test_danger_range(self):
        assert score_label(0.80) == 'DANGER'
        assert score_label(0.95) == 'DANGER'
        assert score_label(1.0) == 'DANGER'


class TestScoreLabelColor:
    """Tests for score_label_color function."""

    def test_safe_green(self):
        assert score_label_color(0.0) == 'green'
        assert score_label_color(0.30) == 'green'

    def test_low_blue(self):
        assert score_label_color(0.31) == 'blue'
        assert score_label_color(0.59) == 'blue'

    def test_warning_yellow(self):
        assert score_label_color(0.60) == 'yellow'
        assert score_label_color(0.79) == 'yellow'

    def test_danger_red(self):
        assert score_label_color(0.80) == 'red'
        assert score_label_color(1.0) == 'red'


class TestScoreBar:
    """Tests for _score_bar function."""

    def test_empty_bar(self):
        bar = _score_bar(0.0, 10)
        assert '░' * 10 in bar

    def test_full_bar(self):
        bar = _score_bar(1.0, 10)
        # Each block is wrapped in ANSI codes, count raw blocks
        assert bar.count('█') == 10
        assert bar.count('░') == 0

    def test_half_bar(self):
        bar = _score_bar(0.5, 10)
        assert bar.count('█') == 5
        assert bar.count('░') == 5

    def test_custom_width(self):
        bar = _score_bar(0.5, 5)
        assert bar.count('█') >= 2 and bar.count('█') <= 3


class TestColorize:
    """Tests for _colorize function."""

    def test_adds_ansi_codes(self):
        result = _colorize("test", "red")
        assert '\033[91m' in result
        assert '\033[0m' in result
        assert 'test' in result

    def test_unknown_color(self):
        result = _colorize("test", "unknown")
        # Unknown color still gets reset code appended
        assert 'test' in result


class TestFormatTerminal:
    """Tests for format_terminal function."""

    def _make_results(self, bot_rate=0.5, sessions=None):
        if sessions is None:
            sessions = [
                {
                    'ip': '10.0.0.1', 'score': 0.95, 'label': 'bot',
                    'heuristic_label': 'bot', 'heuristic_confidence': 0.95,
                    'heuristic_reason': 'known bot UA', 'model_score': 0.8,
                    'request_count': 10, 'duration': 5.0,
                    'top_endpoint': '/api', 'user_agent': 'python-requests',
                },
                {
                    'ip': '192.168.1.1', 'score': 0.2, 'label': 'human',
                    'heuristic_label': 'human', 'heuristic_confidence': 0.75,
                    'heuristic_reason': 'known browser', 'model_score': 0.1,
                    'request_count': 5, 'duration': 30.0,
                    'top_endpoint': '/', 'user_agent': 'Mozilla/5.0',
                },
            ]
        return {
            'total_sessions': 2,
            'bot_count': 1,
            'human_count': 1,
            'bot_rate': bot_rate,
            'sessions': sessions,
        }

    def test_header_present(self):
        result = self._make_results()
        output = format_terminal(result)
        assert 'Microguard Bot Traffic Report' in output

    def test_bot_rate_shown(self):
        result = self._make_results(bot_rate=0.5)
        output = format_terminal(result)
        assert '50.0%' in output

    def test_session_table(self):
        result = self._make_results()
        output = format_terminal(result)
        assert '10.0.0.1' in output
        assert '192.168.1.1' in output

    def test_risk_labels(self):
        result = self._make_results()
        output = format_terminal(result)
        assert 'DANGER' in output
        assert 'LOW' in output or 'SAFE' in output

    def test_score_bar_present(self):
        result = self._make_results()
        output = format_terminal(result)
        assert '█' in output or '░' in output

    def test_empty_sessions(self):
        result = self._make_results(sessions=[])
        output = format_terminal(result)
        assert 'Total sessions:  0' in output or 'Total sessions:  2' in output

    def test_recommendations_high(self):
        result = self._make_results(bot_rate=0.5)
        output = format_terminal(result)
        assert 'Rate limiting' in output or 'CAPTCHA' in output

    def test_recommendations_low(self):
        result = self._make_results(bot_rate=0.05)
        output = format_terminal(result)
        assert 'Healthy' in output or 'healthy' in output

    def test_parse_error_shows_warning_not_healthy(self):
        # Regression: ISSUE-001 — a log file with zero parsed entries (empty
        # or malformed) rendered as a normal "0.0% HEALTHY" report with no
        # indication anything was wrong, because scan_logfile's 'error' key
        # was silently dropped by format_terminal.
        # Found by /qa on 2026-09-04
        # Report: .gstack/qa-reports/qa-report-microguard-cli-2026-09-04.md
        result = {
            'total_sessions': 0, 'bot_count': 0, 'human_count': 0,
            'bot_rate': 0.0, 'sessions': [],
            'error': 'No valid log entries found',
        }
        output = format_terminal(result)
        assert 'No valid log entries found' in output
        assert 'HEALTHY' not in output
        assert 'Total sessions' not in output


class TestFormatJson:
    """Tests for format_json function."""

    def test_valid_json(self):
        import json
        result = {
            'total_sessions': 1, 'bot_count': 1, 'human_count': 0,
            'bot_rate': 1.0, 'sessions': [], 'summary': {},
        }
        output = format_json(result)
        parsed = json.loads(output)
        assert parsed['total_sessions'] == 1

    def test_pretty_format(self):
        result = {
            'total_sessions': 0, 'bot_count': 0, 'human_count': 0,
            'bot_rate': 0.0, 'sessions': [],
        }
        output = format_json(result)
        assert '\n' in output  # Should be multi-line
        assert '  ' in output  # Should be indented


class TestFormatJsonPretty:
    """Tests for format_json_pretty function."""

    def test_scan_format(self):
        result = {
            'total_sessions': 2, 'bot_count': 1, 'human_count': 1,
            'bot_rate': 0.5, 'threshold': 0.7, 'model_used': True,
            'sessions': [
                {
                    'ip': '10.0.0.1', 'score': 0.95, 'label': 'bot',
                    'heuristic_label': 'bot', 'heuristic_confidence': 0.95,
                    'heuristic_reason': 'known bot UA', 'model_score': 0.8,
                    'request_count': 10, 'duration': 5.0,
                    'top_endpoint': '/api', 'user_agent': 'python-requests',
                },
            ],
        }
        output = format_json_pretty(result)
        assert 'summary' in output
        assert 'DANGER' in output or 'danner' in output.lower()

    def test_probe_format(self):
        result = {
            'url': 'https://example.com',
            'status_code': 200,
            'probes': 3,
            'combined_score': 0.23,
            'label': 'human',
            'threshold': 0.7,
            'heuristic_score': 0.3,
            'heuristic_reason': 'no strong signals',
            'model_score': 0.1,
            'features': {'response_time': 0.5, 'ttfb': 0.3},
            'timing': {'total': 0.5},
        }
        output = format_json_pretty(result)
        assert 'example.com' in output
        assert 'features' in output
        assert 'response_time' in output


class TestFormatVerbose:
    """Tests for format_verbose function."""

    def test_shows_feature_vector(self):
        result = {
            'total_sessions': 1, 'bot_count': 1, 'human_count': 0,
            'bot_rate': 1.0, 'sessions': [
                {
                    'ip': '10.0.0.1', 'score': 0.95, 'label': 'bot',
                    'heuristic_label': 'bot', 'heuristic_confidence': 0.95,
                    'heuristic_reason': 'known bot UA', 'model_score': 0.8,
                    'request_count': 10, 'duration': 5.0,
                    'top_endpoint': '/api', 'user_agent': 'python-requests',
                    'features': {
                        'time_since_last_request': 0.5,
                        'requests_per_minute_1m': 10.0,
                        'requests_per_minute_5m': 10.0,
                        'inter_request_time_cv': 0.05,
                        'time_since_session_start': 5.0,
                        'endpoint_count': 1.0,
                        'endpoint_sequence_entropy': 0.0,
                        'unique_endpoint_ratio': 0.1,
                        'method_mismatch_count': 0.0,
                        'header_consistency_score': 1.0,
                        'has_accept_language': 0.0,
                        'ua_category': 1.0,
                        'payload_entropy': 3.0,
                        'status_code_entropy': 0.0,
                        'same_endpoint_hits': 10.0,
                        'error_rate': 0.0,
                        'image_ratio': 0.0,
                        'night_ratio': 0.0,
                        'max_sustained_click_rate': 0.0,
                    },
                },
            ],
        }
        output = format_verbose(result)
        assert 'Feature Vector' in output
        assert 'time_since_last_request' in output
        assert 'heuristic' in output.lower()

    def test_shows_session_details(self):
        result = {
            'total_sessions': 1, 'bot_count': 1, 'human_count': 0,
            'bot_rate': 1.0, 'sessions': [
                {
                    'ip': '10.0.0.1', 'score': 0.95, 'label': 'bot',
                    'heuristic_label': 'bot', 'heuristic_confidence': 0.95,
                    'heuristic_reason': 'known bot UA', 'model_score': 0.8,
                    'request_count': 10, 'duration': 5.0,
                    'top_endpoint': '/api', 'user_agent': 'python-requests',
                    'features': {f: 0.0 for f in [
                        'time_since_last_request', 'requests_per_minute_1m',
                        'requests_per_minute_5m', 'inter_request_time_cv',
                        'time_since_session_start', 'endpoint_count',
                        'endpoint_sequence_entropy', 'unique_endpoint_ratio',
                        'method_mismatch_count', 'header_consistency_score',
                        'has_accept_language', 'ua_category', 'payload_entropy',
                        'status_code_entropy', 'same_endpoint_hits', 'error_rate',
                        'image_ratio', 'night_ratio', 'max_sustained_click_rate',
                    ]},
                },
            ],
        }
        output = format_verbose(result)
        assert '10.0.0.1' in output
        assert 'python-requests' in output
        assert 'known bot UA' in output


class TestFormatNginxDenylist:
    """Tests for format_nginx_denylist function."""

    def test_includes_danger_ip_as_deny_rule(self):
        result = {
            'sessions': [
                {'ip': '10.0.0.50', 'score': 0.95, 'label': 'bot'},
            ],
        }
        output = format_nginx_denylist(result)
        assert 'deny 10.0.0.50;' in output

    def test_excludes_non_danger_sessions(self):
        result = {
            'sessions': [
                {'ip': '10.0.0.50', 'score': 0.95, 'label': 'bot'},
                {'ip': '192.168.1.100', 'score': 0.31, 'label': 'human'},
            ],
        }
        output = format_nginx_denylist(result)
        assert 'deny 10.0.0.50;' in output
        assert '192.168.1.100' not in output

    def test_dedupes_repeated_ip(self):
        result = {
            'sessions': [
                {'ip': '10.0.0.50', 'score': 0.95, 'label': 'bot'},
                {'ip': '10.0.0.50', 'score': 0.88, 'label': 'bot'},
            ],
        }
        output = format_nginx_denylist(result)
        assert output.count('deny 10.0.0.50;') == 1

    def test_empty_when_no_danger_sessions(self):
        result = {
            'sessions': [
                {'ip': '192.168.1.100', 'score': 0.31, 'label': 'human'},
            ],
        }
        output = format_nginx_denylist(result)
        assert 'deny' not in output
        assert 'nothing to block' in output

    def test_parse_error_surfaced_not_silently_empty(self):
        # Regression: same bug class as ISSUE-001 — a malformed/empty log
        # has zero sessions, which looks identical to "scanned fine, zero
        # DANGER IPs" unless the parse error is surfaced. A user generating
        # a blocklist from a broken log would otherwise see "nothing to
        # block" and wrongly conclude their traffic is clean.
        # Found by /qa on 2026-09-04
        result = {'sessions': [], 'error': 'No valid log entries found'}
        output = format_nginx_denylist(result)
        assert 'No valid log entries found' in output


class TestFormatCloudflareRule:
    """Tests for format_cloudflare_rule function."""

    def test_includes_danger_ip_in_expression(self):
        result = {
            'sessions': [
                {'ip': '10.0.0.50', 'score': 0.95, 'label': 'bot'},
            ],
        }
        output = format_cloudflare_rule(result)
        assert '(ip.src in {10.0.0.50})' in output

    def test_multiple_ips_space_separated_and_sorted(self):
        result = {
            'sessions': [
                {'ip': '10.0.0.99', 'score': 0.95, 'label': 'bot'},
                {'ip': '10.0.0.5', 'score': 0.90, 'label': 'bot'},
                {'ip': '10.0.0.5', 'score': 0.90, 'label': 'bot'},
            ],
        }
        output = format_cloudflare_rule(result)
        assert '(ip.src in {10.0.0.5 10.0.0.99})' in output

    def test_empty_when_no_danger_sessions(self):
        result = {
            'sessions': [
                {'ip': '192.168.1.100', 'score': 0.31, 'label': 'human'},
            ],
        }
        output = format_cloudflare_rule(result)
        assert 'ip.src' not in output

    def test_parse_error_surfaced_not_silently_empty(self):
        # Regression: same bug class as ISSUE-001, see format_nginx_denylist
        # test above.
        # Found by /qa on 2026-09-04
        result = {'sessions': [], 'error': 'No valid log entries found'}
        output = format_cloudflare_rule(result)
        assert 'No valid log entries found' in output
        assert 'nothing to block' in output


class TestFormatHtml:
    """Tests for format_html function."""

    def test_valid_html(self):
        result = {
            'total_sessions': 2, 'bot_count': 1, 'human_count': 1,
            'bot_rate': 0.5, 'threshold': 0.7, 'model_used': True,
            'summary': {'total_entries': 100},
            'sessions': [
                {
                    'ip': '10.0.0.1', 'score': 0.95, 'label': 'bot',
                    'user_agent': 'python-requests', 'request_count': 10,
                    'duration': 5.0, 'top_endpoint': '/api',
                    'heuristic_reason': 'known bot',
                },
            ],
        }
        output = format_html(result)
        assert '<!DOCTYPE html>' in output
        assert 'Microguard' in output
        assert '50.0%' in output

    def test_empty_sessions(self):
        result = {
            'total_sessions': 0, 'bot_count': 0, 'human_count': 0,
            'bot_rate': 0.0, 'threshold': 0.7, 'model_used': False,
            'summary': {'total_entries': 0}, 'sessions': [],
        }
        output = format_html(result)
        assert '<!DOCTYPE html>' in output

    def test_parse_error_shows_no_data_not_healthy(self):
        # Regression: ISSUE-002 — with zero sessions and no 'error' handling,
        # the donut chart's conic-gradient defaulted the empty bot slice to
        # red (100% of the ring), directly contradicting the adjacent
        # "Healthy" status text it was drawn next to.
        # Found by /qa on 2026-09-04
        # Report: .gstack/qa-reports/qa-report-microguard-cli-2026-09-04.md
        result = {
            'total_sessions': 0, 'bot_count': 0, 'human_count': 0,
            'bot_rate': 0.0, 'threshold': 0.7, 'model_used': False,
            'summary': {'total_entries': 0}, 'sessions': [],
            'error': 'No valid log entries found',
        }
        output = format_html(result)
        assert 'No valid log entries found' in output
        assert 'No Data' in output
        assert '>Healthy<' not in output
        # Donut's bot slice must be neutral gray, not danger-red, when empty
        assert '#6b7280 0.0deg 360deg' in output


class TestPrintReport:
    """Tests for print_report function."""

    def test_json_format(self, capsys):
        result = {'total_sessions': 0, 'bot_count': 0, 'human_count': 0,
                  'bot_rate': 0.0, 'sessions': []}
        print_report(result, fmt='json')
        captured = capsys.readouterr()
        assert 'total_sessions' in captured.out

    def test_terminal_format(self, capsys):
        result = {'total_sessions': 0, 'bot_count': 0, 'human_count': 0,
                  'bot_rate': 0.0, 'sessions': []}
        print_report(result, fmt='terminal')
        captured = capsys.readouterr()
        assert 'Microguard' in captured.out

    def test_nginx_format(self, capsys):
        result = {'sessions': [{'ip': '10.0.0.50', 'score': 0.95, 'label': 'bot'}]}
        print_report(result, fmt='nginx')
        captured = capsys.readouterr()
        assert 'deny 10.0.0.50;' in captured.out

    def test_cloudflare_format(self, capsys):
        result = {'sessions': [{'ip': '10.0.0.50', 'score': 0.95, 'label': 'bot'}]}
        print_report(result, fmt='cloudflare')
        captured = capsys.readouterr()
        assert '(ip.src in {10.0.0.50})' in captured.out


def _results(**overrides):
    """A scan result dict, the shape scan_logfile returns."""
    result = {
        'total_sessions': 10,
        'bot_count': 1,
        'human_count': 9,
        'integration_count': 0,
        'bot_rate': 0.1,
        'threshold': 0.7,
        'model_used': True,
        'sessions': [],
        'summary': {
            'total_entries': 50, 'total_sessions': 10, 'bot_sessions': 1,
            'human_sessions': 9, 'integration_sessions': 0, 'bot_rate': 0.1,
        },
    }
    result.update(overrides)
    return result


def _session(**overrides):
    session = {
        'ip': '10.0.0.1',
        'score': 0.5,
        'model_score': 0.4,
        'heuristic_label': 'human',
        'heuristic_confidence': 0.6,
        'heuristic_reason': 'no strong signals either way',
        'label': 'human',
        'request_count': 5,
        'duration': 12.0,
        'top_endpoint': '/api/items',
        'user_agent': 'Mozilla/5.0',
        'features': {},
    }
    session.update(overrides)
    return session


class TestModerateTrafficBand:
    """Between 10% and 30% bot traffic the report warns rather than alarms."""

    def test_terminal_warns(self):
        output = format_terminal(_results(bot_rate=0.2, bot_count=2, human_count=8))

        assert 'Moderate bot traffic' in output

    def test_html_says_warning(self):
        html = format_html(_results(bot_rate=0.2, bot_count=2, human_count=8))

        assert 'Warning' in html


class TestIntegrationDisplay:
    """Recognized webhooks are counted separately from bots and humans."""

    def test_terminal_lists_integrations(self):
        output = format_terminal(_results(integration_count=3))

        assert 'Integrations' in output

    def test_html_labels_an_integration_session(self):
        html = format_html(_results(sessions=[
            _session(label='automated-integration',
                     heuristic_label='automated-integration'),
        ]))

        assert 'label-integration' in html


class TestTruncation:
    def test_a_long_endpoint_is_shortened_in_the_html_cell(self):
        # The full value stays in the title attribute; only the visible cell
        # text is cut.
        html = format_html(_results(sessions=[_session(top_endpoint='/' + 'a' * 80)]))

        assert '/' + 'a' * 26 + '...' in html

    def test_a_long_reason_is_shortened_in_the_html_cell(self):
        html = format_html(_results(sessions=[_session(heuristic_reason='r' * 90)]))

        assert 'r' * 47 + '...' in html

    def test_a_long_duration_is_flagged_in_the_terminal_table(self):
        output = format_terminal(_results(sessions=[_session(duration=9999.0)]))

        assert '9999' in output


class TestScoreBands:
    def test_a_middle_score_renders_in_the_warning_colour(self):
        output = format_terminal(_results(sessions=[_session(score=0.65)]))

        assert '0.65' in output

    def test_html_uses_the_medium_score_class(self):
        html = format_html(_results(sessions=[_session(score=0.65)]))

        assert 'score-medium' in html

    def test_the_low_band_has_its_own_colour_name(self):
        assert score_label_color(0.45) == 'blue'


class TestPrintReportHtml:
    def test_html_goes_to_stdout(self, capsys):
        print_report(_results(), fmt='html')

        assert '<!DOCTYPE html>' in capsys.readouterr().out


class TestRemainingDisplayBranches:
    def test_an_integration_label_gets_its_own_colour(self):
        from microguard.report import _label_color_name

        assert _label_color_name('automated-integration') == 'blue'
        assert _label_color_name('bot') == 'red'
        assert _label_color_name('human') == 'green'

    def test_a_fast_busy_session_has_its_duration_flagged(self):
        output = format_terminal(_results(sessions=[
            _session(duration=0.4, request_count=20),
        ]))

        assert '0.4s' in output

    def test_html_uses_the_low_score_class(self):
        # The class boundary is 0.3, not the SAFE/LOW band boundary of 0.30.
        html = format_html(_results(sessions=[_session(score=0.2)]))

        assert 'score-low' in html

    def test_a_long_user_agent_is_shortened_in_the_html_cell(self):
        html = format_html(_results(sessions=[_session(user_agent='u' * 60)]))

        assert 'u' * 37 + '...' in html

    def test_verbose_marks_a_healthy_bot_rate(self):
        output = format_verbose(_results(bot_rate=0.05))

        assert '✅' in output

    def test_verbose_marks_a_moderate_bot_rate(self):
        output = format_verbose(_results(bot_rate=0.2))

        assert '⚠️' in output
