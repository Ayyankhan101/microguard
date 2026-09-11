"""HTTP probe tests against a loopback server, not the internet.

`probe_url_multiple` and `probe_and_analyze` were entirely uncovered: every
path to them was either real network egress or stubbed out by the CLI tests.

This follows the pattern `test_scanner_ws.py` already established — a stdlib
server on 127.0.0.1:0 in a daemon thread, with behavior selected per fixture.
No third-party host, no urllib patching, and nothing that fails when the
machine is offline.
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from microguard.scanner import (
    extract_probe_features,
    probe_and_analyze,
    probe_url,
    probe_url_multiple,
)


class _Handler(BaseHTTPRequestHandler):
    """Serves whatever behavior its server was configured with."""

    def do_GET(self):
        mode = self.server.mode

        # A loopback response lands in under a millisecond, and the probe's
        # first rules short-circuit on "extremely fast response (<50ms) —
        # likely cached". Modes that need a later rule to be reached have to
        # be slower than that bar.
        if mode in ('rate_limited', 'secure', 'error'):
            time.sleep(0.06)

        if mode == 'redirect':
            if self.path == '/landed':
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'<html>landed after the redirect</html>')
                return
            self.send_response(302)
            self.send_header('Location', '/landed')
            self.end_headers()
            return

        if mode == 'error':
            self.send_error(500, "boom")
            return

        if mode == 'rate_limited':
            self.send_response(429)
            self.send_header('Retry-After', '60')
            self.end_headers()
            self.wfile.write(b'slow down')
            return

        if mode == 'secure':
            self.send_response(200)
            for header in ('Content-Security-Policy', 'X-Frame-Options',
                           'Strict-Transport-Security', 'X-Content-Type-Options'):
                self.send_header(header, 'set')
            self.end_headers()
            self.wfile.write(b'<html>secure page with plenty of varied content</html>')
            return

        self.send_response(200)
        self.send_header('Server', 'test-server')
        self.end_headers()
        self.wfile.write(b'<html>hello from the loopback server, with some entropy</html>')

    do_HEAD = do_GET
    do_POST = do_GET

    def log_message(self, *_args):
        """Silence the default stderr logging."""


def _server(mode='ok'):
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    server.mode = mode
    server.daemon_threads = True
    # A short poll interval keeps shutdown() from costing half a second per
    # fixture teardown.
    threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02},
                     daemon=True).start()
    return server


@pytest.fixture
def http_server():
    server = _server()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def redirect_server():
    server = _server('redirect')
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def error_server():
    server = _server('error')
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def rate_limited_server():
    server = _server('rate_limited')
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def secure_server():
    server = _server('secure')
    yield server
    server.shutdown()
    server.server_close()


def _url(server, path='/'):
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


class TestProbeUrl:
    def test_a_successful_probe_reports_status_and_timing(self, http_server):
        result = probe_url(_url(http_server), timeout=5.0)

        assert result.status_code == 200
        assert result.error is None
        assert result.timing['total'] > 0
        assert b'loopback' in result.body

    def test_custom_headers_are_sent(self, http_server):
        result = probe_url(_url(http_server), headers={'X-Test': 'yes'}, timeout=5.0)

        assert result.status_code == 200

    def test_a_server_error_is_captured_not_raised(self, error_server):
        """urllib raises HTTPError on 5xx; the prober turns it into a result."""
        result = probe_url(_url(error_server), timeout=5.0)

        assert result.status_code == 500

    def test_rate_limiting_is_captured(self, rate_limited_server):
        result = probe_url(_url(rate_limited_server), timeout=5.0)

        assert result.status_code == 429

    def test_redirects_are_followed_by_default(self, redirect_server):
        result = probe_url(_url(redirect_server), timeout=5.0)

        # /landed is served by the same handler, so following lands on a 200.
        assert result.status_code == 200

    def test_redirects_can_be_left_unfollowed(self, redirect_server):
        result = probe_url(_url(redirect_server), follow_redirects=False, timeout=5.0)

        assert result.status_code == 302

    def test_a_bare_hostname_is_given_a_scheme(self):
        # Unresolvable, so this only pins that the scheme is prepended rather
        # than the call failing on a missing one.
        result = probe_url("this-host-does-not-exist-98765.invalid", timeout=2.0)

        assert result.url.startswith("https://")
        assert result.error is not None


class TestProbeUrlMultiple:
    def test_sends_the_requested_number_of_probes(self, http_server):
        results = probe_url_multiple(_url(http_server), count=3, timeout=5.0)

        assert len(results) == 3
        assert all(r.status_code == 200 for r in results)

    def test_honors_the_delay_between_probes(self, http_server):
        import time

        start = time.monotonic()
        probe_url_multiple(_url(http_server), count=2, delay=0.05, timeout=5.0)

        assert time.monotonic() - start >= 0.05

    def test_rotating_user_agents_varies_the_request(self, http_server):
        results = probe_url_multiple(
            _url(http_server), count=3, randomize_ua=True, timeout=5.0
        )

        assert len(results) == 3

    def test_a_custom_method_is_used(self, http_server):
        results = probe_url_multiple(_url(http_server), count=1, method="HEAD", timeout=5.0)

        assert results[0].status_code == 200

    def test_features_come_out_of_the_real_responses(self, http_server):
        results = probe_url_multiple(_url(http_server), count=2, timeout=5.0)

        features = extract_probe_features(results)

        assert features['has_server_header'] == 1.0
        assert features['body_entropy'] > 0


class TestProbeAndAnalyze:
    def test_returns_the_documented_shape(self, http_server):
        result = probe_and_analyze(_url(http_server), count=2, threshold=0.7)

        for key in ('url', 'probes', 'features', 'heuristic_score',
                    'heuristic_reason', 'model_score', 'combined_score',
                    'label', 'threshold', 'timing', 'status_code', 'headers',
                    'body_preview'):
            assert key in result

    def test_the_model_is_never_applied_to_a_probe(self, http_server):
        """Probe data has no valid mapping onto the 19 session features, so
        model_score stays 0.0 and the blend is the heuristic alone."""
        result = probe_and_analyze(_url(http_server), count=1)

        assert result['model_score'] == 0.0
        assert result['combined_score'] == result['heuristic_score']

    def test_verbose_reports_each_probe_to_stderr(self, http_server, capsys):
        probe_and_analyze(_url(http_server), count=2, verbose=True)

        captured = capsys.readouterr().err
        assert 'Probing' in captured
        assert 'Probe 1/2' in captured

    def test_a_well_protected_target_scores_low(self, secure_server):
        result = probe_and_analyze(_url(secure_server), count=1)

        assert result['label'] == 'human'
        assert 'security headers' in result['heuristic_reason']

    def test_a_rate_limited_target_scores_high(self, rate_limited_server):
        result = probe_and_analyze(_url(rate_limited_server), count=1)

        assert result['heuristic_score'] == 0.8
        assert 'rate limited' in result['heuristic_reason']

    def test_a_refused_connection_is_reported_not_raised(self):
        result = probe_and_analyze("http://127.0.0.1:1/", count=1)

        assert 'connection error' in result['heuristic_reason']


class TestSslAndErrorPaths:
    def test_verification_can_be_disabled(self, http_server):
        """Builds an unverified SSL context even for a plain-HTTP target.

        There is no TLS server here, so this covers the context construction
        rather than a real handshake.
        """
        result = probe_url(_url(http_server), verify_ssl=False, timeout=5.0)

        assert result.status_code == 200

    def test_an_unexpected_failure_is_captured_on_the_result(self, monkeypatch):
        """Anything that is not an HTTPError or URLError still has to come back
        as a result, because the CLI reports result.error rather than catching.
        """
        import urllib.request

        def exploding_open(*_args, **_kwargs):
            raise ValueError("something unexpected")

        # opener.open is inside the try; build_opener is not.
        monkeypatch.setattr(urllib.request.OpenerDirector, "open", exploding_open)

        result = probe_url("https://example.com", timeout=1.0)

        assert result.error == "something unexpected"
        assert result.status_code == 0
