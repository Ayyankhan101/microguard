"""Tests for the watch module."""

import threading
import time
from datetime import datetime, timezone

from microguard.cli import DEFAULT_MODEL_PATH
from microguard.parser import LogEntry
from microguard.watch import _format_detection, _read_new_lines, watch_logfile

NGINX_LINE = (
    '192.168.1.100 - - [24/Mar/2023:17:07:41 +0000] '
    '"GET /products HTTP/1.1" 200 1234 '
    '"https://example.com" '
    '"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"'
)

NGINX_BOT = (
    '10.0.0.1 - - [24/Mar/2023:17:07:41 +0000] '
    '"GET /api/data HTTP/1.1" 200 5678 '
    '"-" '
    '"python-requests/2.28.0"'
)


class TestReadNewLines:
    """Tests for _read_new_lines function."""

    def test_read_from_empty_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        entries, offset = _read_new_lines(str(log_file), 0, 'nginx')
        assert entries == []
        assert offset == 0

    def test_read_new_entries(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(NGINX_HUMAN + "\n" + NGINX_BOT + "\n")
        entries, _offset = _read_new_lines(str(log_file), 0, 'nginx')
        assert len(entries) == 2
        assert entries[0].ip == '192.168.1.100'
        assert entries[1].ip == '10.0.0.1'

    def test_read_appended_entries(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(NGINX_HUMAN + "\n")
        entries1, offset1 = _read_new_lines(str(log_file), 0, 'nginx')
        assert len(entries1) == 1

        # Append new entry
        with open(str(log_file), 'a') as f:
            f.write(NGINX_BOT + "\n")

        entries2, offset2 = _read_new_lines(str(log_file), offset1, 'nginx')
        assert len(entries2) == 1
        assert entries2[0].ip == '10.0.0.1'
        assert offset2 > offset1

    def test_no_new_entries(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(NGINX_HUMAN + "\n")
        _entries1, offset1 = _read_new_lines(str(log_file), 0, 'nginx')
        entries2, offset2 = _read_new_lines(str(log_file), offset1, 'nginx')
        assert entries2 == []
        assert offset2 == offset1

    def test_missing_file(self):
        entries, offset = _read_new_lines("/nonexistent/file.log", 0, 'nginx')
        assert entries == []
        assert offset == 0

    def test_json_format(self, tmp_path):
        log_file = tmp_path / "test.log"
        json_line = (
            '{"remote_addr": "10.0.0.1", "time_local": "24/Mar/2023:17:07:41 +0000", '
            '"method": "GET", "url": "/api", "status": 200, "body_bytes_sent": 100, '
            '"http_referer": "-", "http_user_agent": "python-requests/2.28.0"}'
        )
        log_file.write_text(json_line + "\n")
        entries, _offset = _read_new_lines(str(log_file), 0, 'json')
        assert len(entries) == 1
        assert entries[0].ip == '10.0.0.1'


class TestFormatDetection:
    """Tests for _format_detection function."""

    def test_bot_detection_format(self):
        entry = LogEntry(
            ip="10.0.0.1",
            timestamp=datetime(2023, 3, 24, 17, 7, 41, tzinfo=timezone.utc),
            method="GET",
            url="/api/data",
            status=200,
            size=5678,
            referer="-",
            user_agent="python-requests/2.28.0",
        )
        output = _format_detection(
            entry, "bot", 0.95, "known bot UA", 0.8, 0.95
        )
        assert "Bot Detection" in output
        assert "10.0.0.1" in output
        assert "DANGER" in output or "danner" in output.lower()
        assert "python-requests" in output
        assert "known bot UA" in output

    def test_human_detection_format(self):
        entry = LogEntry(
            ip="192.168.1.1",
            timestamp=datetime(2023, 3, 24, 17, 7, 41, tzinfo=timezone.utc),
            method="GET",
            url="/products",
            status=200,
            size=1234,
            referer="https://example.com",
            user_agent="Mozilla/5.0 Chrome/120.0.0.0",
        )
        output = _format_detection(
            entry, "human", 0.75, "known browser", 0.1, 0.25
        )
        assert "192.168.1.1" in output
        assert "SAFE" in output or "LOW" in output
        assert "Mozilla" in output


def _graphql_lines(ip="10.1.1.1"):
    """25 legitimate GraphQL calls — single endpoint, browser UA.

    Same regression fixture shape as test_cli.py's, adapted for watch
    mode's own (separate, duplicated) score-blending implementation.
    """
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    lines = []
    for i in range(25):
        second = int(i * 2.3) % 60
        minute = 12 * 60 + int(i * 2.3) // 60
        ts = f"24/Mar/2024:{minute // 60:02d}:{minute % 60:02d}:{second:02d} +0000"
        lines.append(
            f'{ip} - - [{ts}] "POST /graphql HTTP/1.1" 200 512 '
            f'"https://app.example.com/" "{ua}"'
        )
    return lines


class TestWatchLogfile:
    """End-to-end tests for the watch loop's orchestration and scoring.

    watch_logfile() blocks in a `while True` tailing loop — these tests
    append to the watched file from a background thread while the loop
    runs, bounded by `_max_iterations` (a test-only seam) instead of
    relying on KeyboardInterrupt.
    """

    def _run_with_append(self, log_file, lines, **kwargs):
        def append_later():
            time.sleep(0.05)
            with open(log_file, 'a') as f:
                f.write("\n".join(lines) + "\n")

        t = threading.Thread(target=append_later)
        t.start()
        watch_logfile(
            str(log_file), model_path=DEFAULT_MODEL_PATH,
            interval=0.02, _max_iterations=15, **kwargs,
        )
        t.join()

    def test_detects_and_prints_obvious_bot(self, tmp_path, capsys):
        log_file = tmp_path / "watch.log"
        log_file.write_text("")
        self._run_with_append(log_file, [NGINX_BOT])
        output = capsys.readouterr().out
        assert "10.0.0.1" in output
        assert "python-requests" in output

    def test_score_blending_symmetry_graphql_not_flagged(self, tmp_path, capsys):
        """Regression test: watch.py has its own copy of the heuristic<->model
        score-blending logic (duplicated from cli.py, same asymmetry bug
        found and fixed there this session). A GraphQL session must not be
        printed as a bot detection — human sessions produce no per-entry
        print at all, so the IP should not appear in output."""
        log_file = tmp_path / "watch.log"
        log_file.write_text("")
        self._run_with_append(log_file, _graphql_lines(ip="10.1.1.1"))
        output = capsys.readouterr().out
        assert "10.1.1.1" not in output

    def test_stops_after_max_iterations_without_keyboard_interrupt(self, tmp_path):
        log_file = tmp_path / "watch.log"
        log_file.write_text("")
        # Must return on its own — no KeyboardInterrupt raised, no hang.
        watch_logfile(str(log_file), interval=0.01, _max_iterations=3)

    def test_missing_file_at_start_does_not_crash(self, tmp_path, capsys):
        log_file = tmp_path / "does-not-exist.log"
        watch_logfile(str(log_file), interval=0.01, _max_iterations=2)
        assert "not found" in capsys.readouterr().err.lower()


# Use the same constant that was defined above
NGINX_HUMAN = NGINX_LINE
