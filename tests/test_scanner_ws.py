"""Tests for WebSocket probing (microguard.scanner).

Uses a tiny in-process, stdlib-only RFC 6455 echo server (socket + threading)
rather than a public echo endpoint, matching this project's zero-dependency
ethos and avoiding flaky-network test failures.
"""

import base64
import hashlib
import socket
import struct
import threading
import time

import pytest

from microguard.scanner import (
    _WS_GUID,
    WSProbeResult,
    _analyze_ws_probes,
    _ws_decode_frame,
    _ws_encode_frame,
    extract_ws_probe_features,
    format_ws_probe_html,
    format_ws_probe_report,
    probe_websocket,
    probe_ws_and_analyze,
)


def _ws_accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + _WS_GUID).encode()).digest()).decode()


class _EchoWSServer:
    """Minimal single-connection RFC 6455 server for tests."""

    def __init__(self, reject: bool = False, silent: bool = False):
        self.reject = reject
        self.silent = silent  # accept the handshake but never send a frame back
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve_once, daemon=True)
        self._thread.start()

    def _serve_once(self):
        try:
            self._sock.settimeout(5.0)
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            conn.settimeout(5.0)
            data = b''
            while b'\r\n\r\n' not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk

            if self.reject:
                conn.sendall(b'HTTP/1.1 400 Bad Request\r\n\r\n')
                return

            headers_text = data.split(b'\r\n\r\n')[0].decode('iso-8859-1')
            client_key = ''
            for line in headers_text.split('\r\n'):
                if line.lower().startswith('sec-websocket-key:'):
                    client_key = line.split(':', 1)[1].strip()
            accept = _ws_accept_key(client_key)
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            )
            conn.sendall(response.encode())

            if self.silent:
                time.sleep(0.3)
                return

            # Read one (masked) client frame, echo its payload back unmasked.
            frame = _ws_decode_frame(conn, 5.0)
            if frame is not None:
                _opcode, payload = frame
                fin_opcode = 0x80 | 0x1
                length = len(payload)
                if length <= 125:
                    header = struct.pack('!BB', fin_opcode, length)
                else:
                    header = struct.pack('!BBH', fin_opcode, 126, length)
                conn.sendall(header + payload)
        finally:
            conn.close()

    def close(self):
        self._sock.close()


@pytest.fixture
def echo_server():
    server = _EchoWSServer()
    yield server
    server.close()


@pytest.fixture
def rejecting_server():
    server = _EchoWSServer(reject=True)
    yield server
    server.close()


@pytest.fixture
def silent_server():
    server = _EchoWSServer(silent=True)
    yield server
    server.close()


class TestFrameEncodeDecode:
    def test_masked_text_frame_header(self):
        encoded = _ws_encode_frame(b'hello')
        assert encoded[0] == 0x81  # FIN + text opcode
        assert encoded[1] & 0x80  # client frames must be masked
        assert (encoded[1] & 0x7F) == 5

    def test_long_payload_uses_extended_length(self):
        encoded = _ws_encode_frame(b'x' * 200)
        assert (encoded[1] & 0x7F) == 126


class TestProbeWebsocketHandshake:
    def test_successful_handshake_and_echo(self, echo_server):
        result = probe_websocket(f"ws://127.0.0.1:{echo_server.port}/", timeout=5.0)
        assert result.connected
        assert result.handshake_ok
        assert result.status_code == 101
        assert result.frames_received == [b'ping']

    def test_rejected_handshake(self, rejecting_server):
        result = probe_websocket(f"ws://127.0.0.1:{rejecting_server.port}/", timeout=5.0)
        assert result.connected
        assert not result.handshake_ok
        assert result.status_code == 400

    def test_silent_server_no_frame(self, silent_server):
        result = probe_websocket(f"ws://127.0.0.1:{silent_server.port}/", timeout=1.0)
        assert result.handshake_ok
        assert result.frames_received == []

    def test_connection_refused(self):
        result = probe_websocket("ws://127.0.0.1:1/", timeout=1.0)
        assert not result.connected
        assert result.error


class TestAnalyzeWsProbes:
    def test_empty_results(self):
        score, reason = _analyze_ws_probes([], {})
        assert 0.0 <= score <= 1.0
        assert 'no probe results' in reason

    def test_connection_failed(self):
        r = WSProbeResult(url="ws://x", connected=False, error="refused")
        _score, reason = _analyze_ws_probes([r], {})
        assert 'connection failed' in reason

    def test_handshake_rejected(self):
        r = WSProbeResult(url="ws://x", connected=True, handshake_ok=False, status_code=400)
        _score, reason = _analyze_ws_probes([r], {})
        assert 'rejected' in reason or 'malformed' in reason

    def test_clean_handshake_no_frame(self):
        r = WSProbeResult(url="ws://x", connected=True, handshake_ok=True, timing={'handshake': 0.05})
        score, _reason = _analyze_ws_probes([r], {})
        assert 0.0 <= score <= 1.0


class TestExtractWsProbeFeatures:
    def test_empty_results(self):
        assert extract_ws_probe_features([]) == {}

    def test_basic_features(self):
        r = WSProbeResult(
            url="ws://x", connected=True, handshake_ok=True,
            timing={'handshake': 0.02, 'connect': 0.01},
            frames_received=[b'pong'],
        )
        features = extract_ws_probe_features([r])
        assert features['handshake_ok'] == 1.0
        assert features['got_response_frame'] == 1.0
        assert features['frame_entropy'] > 0


class TestProbeWsAndAnalyze:
    def test_end_to_end(self, echo_server):
        result = probe_ws_and_analyze(f"ws://127.0.0.1:{echo_server.port}/", count=1, timeout=5.0)
        assert result['protocol'] == 'websocket'
        assert result['connected']
        assert result['handshake_ok']
        assert 0.0 <= result['combined_score'] <= 1.0
        assert result['model_score'] == 0.0


class TestFormatters:
    def test_format_report(self, echo_server):
        result = probe_ws_and_analyze(f"ws://127.0.0.1:{echo_server.port}/", count=1, timeout=5.0)
        output = format_ws_probe_report(result)
        assert 'WebSocket Probe' in output
        assert 'Automation Fingerprint' in output

    def test_format_html(self, echo_server):
        result = probe_ws_and_analyze(f"ws://127.0.0.1:{echo_server.port}/", count=1, timeout=5.0)
        html = format_ws_probe_html(result)
        assert '<html' in html
        assert 'WebSocket Probe' in html
