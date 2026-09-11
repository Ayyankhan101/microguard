"""Tests for live/server.py — CheckHandler and run_server."""

import io
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from microguard.live.server import CheckHandler, main, run_server


def _decision(label="human", score=0.2, reason="no strong signals", request_count=1,
              model_score=0.0, heuristic_label=None, heuristic_confidence=0.5):
    """A full LiveScorer.score_request payload.

    Mirrors the real shape rather than a subset: a double that returns fewer
    keys than production lets a consumer of the new keys pass its tests and
    then KeyError against the real scorer.
    """
    return {
        "ip": "1.2.3.4",
        "label": label,
        "score": score,
        "model_score": model_score,
        "heuristic_label": heuristic_label or label,
        "heuristic_confidence": heuristic_confidence,
        "heuristic_reason": reason,
        "reason": reason,
        "request_count": request_count,
        "duration": 0.0,
        "model_loaded": True,
    }


def _make_handler(
    path="/check",
    ip="1.2.3.4",
    ua="Mozilla/5.0",
    method="GET",
    url="/api/test",
    xff="",
    xri="",
    scorer=None,
    trust_forwarded_for=False,
):
    """Create a CheckHandler with mock socket for unit testing."""
    handler = CheckHandler.__new__(CheckHandler)
    handler.trust_forwarded_for = trust_forwarded_for
    handler.path = path
    handler.client_address = (ip, 0)
    handler.wfile = io.BytesIO()
    handler.rfile = io.BytesIO()
    handler.command = "GET"

    # Mock headers
    handler.headers = MagicMock()
    handler.headers.get = lambda key, default="": {
        "User-Agent": ua,
        "X-Original-Method": method,
        "X-Original-URI": url,
        "X-Forwarded-For": xff,
        "X-Real-IP": xri,
    }.get(key, default)

    # Mock send_response / send_header / end_headers / send_error
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock(side_effect=RuntimeError("404 sent"))
    handler.log_error = MagicMock()

    # Mock scorer
    if scorer is None:
        scorer = MagicMock()
        scorer.score_request.return_value = _decision()
    handler.scorer = scorer

    return handler


class TestCheckHandler:
    """Unit tests for CheckHandler.do_GET (no network, no Redis)."""

    def test_human_request_returns_200(self):
        handler = _make_handler()
        handler.do_GET()
        handler.send_response.assert_called_with(200)

    def test_bot_request_returns_403(self):
        scorer = MagicMock()
        scorer.score_request.return_value = _decision(
            label="bot", score=0.95, reason="known bot UA", request_count=5, model_score=0.9
        )
        handler = _make_handler(scorer=scorer)
        handler.do_GET()
        handler.send_response.assert_called_with(403)

    def test_wrong_path_returns_404(self):
        handler = _make_handler(path="/missing")
        with pytest.raises(RuntimeError, match="404 sent"):
            handler.do_GET()
        handler.send_error.assert_called_with(404)

    def test_response_includes_json_body(self):
        handler = _make_handler()
        handler.do_GET()
        body = handler.wfile.getvalue()
        data = json.loads(body)
        assert data["label"] == "human"
        assert "score" in data

    def test_response_includes_label_header(self):
        handler = _make_handler()
        handler.do_GET()
        headers = {c.args[0]: c.args[1] for c in handler.send_header.call_args_list}
        assert headers["X-Microguard-Label"] == "human"

    def test_score_header_is_string(self):
        handler = _make_handler()
        handler.do_GET()
        headers = {c.args[0]: c.args[1] for c in handler.send_header.call_args_list}
        assert isinstance(headers["X-Microguard-Score"], str)

    def test_xff_ignored_by_default(self):
        """X-Forwarded-For is client-supplied; trusting it lets a bot rotate sessions."""
        handler = _make_handler(xff="203.0.113.1, 70.41.3.18", ip="10.0.0.1")
        handler.do_GET()
        entry = handler.scorer.score_request.call_args[0][0]
        assert entry.ip == "10.0.0.1"

    def test_xff_used_when_trusted(self):
        handler = _make_handler(xff="203.0.113.1, 70.41.3.18", trust_forwarded_for=True)
        handler.do_GET()
        entry = handler.scorer.score_request.call_args[0][0]
        assert entry.ip == "203.0.113.1"

    def test_xri_wins_over_xff_when_trusted(self):
        """A proxy-set X-Real-IP outranks a forgeable X-Forwarded-For."""
        handler = _make_handler(
            xff="203.0.113.1", xri="198.51.100.1", trust_forwarded_for=True
        )
        handler.do_GET()
        entry = handler.scorer.score_request.call_args[0][0]
        assert entry.ip == "198.51.100.1"

    def test_scoring_failure_fails_open(self):
        """A Redis outage must not 500 the visitor via nginx auth_request."""
        handler = _make_handler()
        handler.scorer.score_request.side_effect = RuntimeError("redis down")
        handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        headers = {c.args[0]: c.args[1] for c in handler.send_header.call_args_list}
        assert headers["X-Microguard-Label"] == "human"

    def test_xri_ip_extraction(self):
        handler = _make_handler(xri="198.51.100.1")
        handler.do_GET()
        entry = handler.scorer.score_request.call_args[0][0]
        assert entry.ip == "198.51.100.1"

    def test_client_ip_fallback(self):
        handler = _make_handler(ip="10.0.0.1")
        handler.do_GET()
        entry = handler.scorer.score_request.call_args[0][0]
        assert entry.ip == "10.0.0.1"

    def test_method_and_url_passed_through(self):
        handler = _make_handler(method="POST", url="/graphql")
        handler.do_GET()
        entry = handler.scorer.score_request.call_args[0][0]
        assert entry.method == "POST"
        assert entry.url == "/graphql"

    def test_user_agent_passed_through(self):
        handler = _make_handler(ua="python-requests/2.28.0")
        handler.do_GET()
        entry = handler.scorer.score_request.call_args[0][0]
        assert entry.user_agent == "python-requests/2.28.0"

    def test_log_message_suppressed(self):
        handler = _make_handler()
        handler.log_message("format", "arg1", "arg2")
        # Should not raise — method exists and suppresses output


class TestRunServer:
    """Test run_server argument wiring (mock Redis)."""

    @patch("microguard.live.server.ThreadingHTTPServer")
    @patch("microguard.live.server.RedisSessionStateStore")
    @patch("microguard.live.server.LiveScorer")
    @patch("microguard.live.server.redis.Redis")
    def test_run_server_wires_args(self, MockRedis, MockScorer, MockStore, MockHTTP):
        mock_r = MagicMock()
        MockRedis.from_url.return_value = mock_r
        MockHTTP.return_value.serve_forever.side_effect = KeyboardInterrupt

        run_server(
            host="0.0.0.0",
            port=9000,
            redis_url="redis://otherhost:6380",
            block_threshold=0.7,
            session_ttl=600,
            trust_forwarded_for=True,
        )

        MockRedis.from_url.assert_called_once_with("redis://otherhost:6380", decode_responses=True)
        mock_r.ping.assert_called_once()
        MockStore.assert_called_once_with(mock_r, default_ttl=600)
        MockScorer.assert_called_once()
        assert MockScorer.call_args[1]["block_threshold"] == 0.7
        MockHTTP.assert_called_once_with(("0.0.0.0", 9000), CheckHandler)
        # threaded, so requests to the protected site do not queue behind
        # each other; daemon so Ctrl-C is not held up by an in-flight request
        assert MockHTTP.return_value.daemon_threads is True


class TestMain:
    """Test main() argument parsing."""

    @patch("microguard.live.server.run_server")
    def test_main_default_args(self, mock_run):
        with patch.object(sys, "argv", ["microguard-serve"]):
            main()
        mock_run.assert_called_once_with(
            host="127.0.0.1",
            port=8400,
            redis_url="redis://localhost:6379",
            block_threshold=0.85,
            session_ttl=1800,
            trust_forwarded_for=False,
        )

    @patch("microguard.live.server.run_server")
    def test_main_custom_args(self, mock_run):
        with patch.object(
            sys,
            "argv",
            [
                "microguard-serve",
                "--host", "0.0.0.0",
                "--port", "9000",
                "--redis-url", "redis://other:6380",
                "--block-threshold", "0.7",
                "--session-ttl", "600",
                "--trust-forwarded-for",
            ],
        ):
            main()
        mock_run.assert_called_once_with(
            host="0.0.0.0",
            port=9000,
            redis_url="redis://other:6380",
            block_threshold=0.7,
            session_ttl=600,
            trust_forwarded_for=True,
        )


class TestRunServerRecording:
    """The check server feeds the dashboard as a side effect of scoring."""

    @patch("microguard.live.server.ThreadingHTTPServer")
    @patch("microguard.live.server.RedisSessionStateStore")
    @patch("microguard.live.server.LiveScorer")
    @patch("microguard.live.server.redis.Redis")
    def test_scorer_gets_a_redis_backed_recorder(
        self, MockRedis, MockScorer, MockStore, MockHTTP
    ):
        from microguard.live.redis_events import RedisDecisionRecorder

        mock_r = MagicMock()
        MockRedis.from_url.return_value = mock_r
        MockHTTP.return_value.serve_forever.side_effect = KeyboardInterrupt

        run_server()

        recorder = MockScorer.call_args[1]["recorder"]
        assert isinstance(recorder, RedisDecisionRecorder)


class TestRunServerRuntimeConfig:
    """A threshold set from the dashboard reaches the running check server."""

    @patch("microguard.live.server.ThreadingHTTPServer")
    @patch("microguard.live.server.RedisSessionStateStore")
    @patch("microguard.live.server.LiveScorer")
    @patch("microguard.live.server.redis.Redis")
    def test_scorer_reads_its_threshold_from_shared_config(
        self, MockRedis, MockScorer, MockStore, MockHTTP
    ):
        mock_r = MagicMock()
        # RedisRuntimeConfig reads through a pipeline, so that is what answers.
        mock_r.pipeline.return_value.execute.return_value = ["0.25"]
        MockRedis.from_url.return_value = mock_r
        MockHTTP.return_value.serve_forever.side_effect = KeyboardInterrupt

        run_server()

        source = MockScorer.call_args[1]["threshold_source"]
        assert source() == 0.25


class TestUnknownRoutes:
    """The check server answers exactly one path: no CORS, no /metrics, no
    health endpoint."""

    def test_the_handler_returns_after_a_404_rather_than_scoring(self):
        """The sibling test above uses a send_error that raises, so it never
        reaches the return. This one lets send_error behave, which proves the
        request is not scored afterwards.
        """
        handler = _make_handler(path="/metrics")
        handler.send_error = MagicMock()

        handler.do_GET()

        handler.send_error.assert_called_once_with(404)
        handler.scorer.score_request.assert_not_called()
