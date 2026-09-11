"""Tests for the log parser module."""

from datetime import datetime, timezone

import pytest

from microguard.parser import (
    LogEntry,
    detect_format,
    parse_file,
    parse_json_line,
    parse_nginx_line,
    parse_string,
)

# Sample Nginx combined log lines
NGINX_HUMAN = (
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

NGINX_EMPTY = ''


# Sample JSON log lines
JSON_HUMAN = (
    '{"remote_addr": "192.168.1.100", "time_local": "24/Mar/2023:17:07:41 +0000", '
    '"method": "GET", "url": "/products", "status": 200, "body_bytes_sent": 1234, '
    '"http_referer": "https://example.com", '
    '"http_user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}'
)

JSON_BOT = (
    '{"remote_addr": "10.0.0.1", "time_local": "24/Mar/2023:17:07:41 +0000", '
    '"method": "GET", "url": "/api/data", "status": 200, "body_bytes_sent": 5678, '
    '"http_referer": "-", "http_user_agent": "python-requests/2.28.0"}'
)


class TestNginxParser:
    """Tests for Nginx combined log parser."""
    
    def test_parse_valid_line(self):
        entry = parse_nginx_line(NGINX_HUMAN)
        assert entry is not None
        assert entry.ip == '192.168.1.100'
        assert entry.method == 'GET'
        assert entry.url == '/products'
        assert entry.status == 200
        assert entry.size == 1234
        assert entry.referer == 'https://example.com'
        assert 'Mozilla' in entry.user_agent
    
    def test_parse_bot_line(self):
        entry = parse_nginx_line(NGINX_BOT)
        assert entry is not None
        assert entry.ip == '10.0.0.1'
        assert entry.method == 'GET'
        assert entry.url == '/api/data'
        assert 'python-requests' in entry.user_agent
    
    def test_parse_empty_line(self):
        entry = parse_nginx_line(NGINX_EMPTY)
        assert entry is None
    
    def test_parse_invalid_line(self):
        entry = parse_nginx_line("not a log line")
        assert entry is None
    
    def test_to_dict(self):
        entry = parse_nginx_line(NGINX_HUMAN)
        d = entry.to_dict()
        assert d['ip'] == '192.168.1.100'
        assert d['method'] == 'GET'
        assert d['status'] == 200
    
    def test_repr(self):
        entry = parse_nginx_line(NGINX_HUMAN)
        assert 'GET' in repr(entry)
        assert '/products' in repr(entry)


class TestJsonParser:
    """Tests for JSON log parser."""
    
    def test_parse_valid_json(self):
        entry = parse_json_line(JSON_HUMAN)
        assert entry is not None
        assert entry.ip == '192.168.1.100'
        assert entry.method == 'GET'
        assert entry.url == '/products'
        assert entry.status == 200
    
    def test_parse_bot_json(self):
        entry = parse_json_line(JSON_BOT)
        assert entry is not None
        assert entry.ip == '10.0.0.1'
        assert 'python-requests' in entry.user_agent
    
    def test_parse_empty_line(self):
        entry = parse_json_line('')
        assert entry is None
    
    def test_parse_invalid_json(self):
        entry = parse_json_line('not json')
        assert entry is None


class TestFormatDetection:
    """Tests for automatic format detection."""
    
    def test_detect_nginx(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(NGINX_HUMAN + "\n" + NGINX_BOT + "\n", encoding='utf-8')
        fmt = detect_format(str(log_file))
        assert fmt == 'nginx'
    
    def test_detect_json(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(JSON_HUMAN + "\n" + JSON_BOT + "\n", encoding='utf-8')
        fmt = detect_format(str(log_file))
        assert fmt == 'json'
    
    def test_detect_unknown(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("random text\nmore random text\n", encoding='utf-8')
        fmt = detect_format(str(log_file))
        assert fmt == 'unknown'


class TestParseFile:
    """Tests for file parsing."""
    
    def test_parse_nginx_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(NGINX_HUMAN + "\n" + NGINX_BOT + "\n", encoding='utf-8')
        
        entries = list(parse_file(str(log_file)))
        assert len(entries) == 2
        assert entries[0].ip == '192.168.1.100'
        assert entries[1].ip == '10.0.0.1'
    
    def test_parse_json_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(JSON_HUMAN + "\n" + JSON_BOT + "\n", encoding='utf-8')
        
        entries = list(parse_file(str(log_file), fmt='json'))
        assert len(entries) == 2
    
    def test_parse_missing_file(self):
        """Asserts the message, not just the type.

        With fmt='auto' the exception comes from detect_format's bare open(, encoding='utf-8')
        and reads "[Errno 2] No such file or directory" — so the handler in
        parse_file that produces the friendly message was never reached, and
        a `pytest.raises(FileNotFoundError)` alone passed anyway. An explicit
        format skips detection and exercises the real path.
        """
        with pytest.raises(FileNotFoundError, match="Log file not found"):
            list(parse_file("/nonexistent/file.log", fmt='nginx'))

    def test_parse_missing_file_during_autodetect(self):
        """The fmt='auto' path fails earlier, in detect_format."""
        with pytest.raises(FileNotFoundError):
            list(parse_file("/nonexistent/file.log"))


class TestParseString:
    """Tests for string parsing."""
    
    def test_parse_nginx_string(self):
        log_text = NGINX_HUMAN + "\n" + NGINX_BOT
        entries = parse_string(log_text, fmt='nginx')
        assert len(entries) == 2
    
    def test_parse_json_string(self):
        log_text = JSON_HUMAN + "\n" + JSON_BOT
        entries = parse_string(log_text, fmt='json')
        assert len(entries) == 2
    
    def test_parse_empty_string(self):
        entries = parse_string("", fmt='nginx')
        assert len(entries) == 0


class TestLogEntrySerialization:
    """to_dict/from_dict are the single codec for a LogEntry.

    The live Redis store persists entries through these, so a field that stops
    round-tripping stops reaching the detection rules. raw_line in particular
    feeds labeler.py's HTTP/1.0 check and was the field the store's old private
    codec existed to carry.
    """

    def test_to_dict_includes_raw_line(self):
        entry = parse_nginx_line(NGINX_HUMAN)
        assert entry.to_dict()['raw_line'] == entry.raw_line

    def test_round_trips_every_field(self):
        entry = parse_nginx_line(NGINX_HUMAN)
        restored = LogEntry.from_dict(entry.to_dict())
        for field in LogEntry.__slots__:
            assert getattr(restored, field) == getattr(entry, field), field

    def test_round_trip_survives_json(self):
        import json as _json

        entry = parse_nginx_line(NGINX_HUMAN)
        restored = LogEntry.from_dict(_json.loads(_json.dumps(entry.to_dict())))
        assert restored.timestamp == entry.timestamp
        assert restored.raw_line == entry.raw_line

    def test_from_dict_tolerates_a_payload_without_raw_line(self):
        """Entries written before raw_line round-tripped must still load."""
        entry = parse_nginx_line(NGINX_HUMAN)
        payload = entry.to_dict()
        del payload['raw_line']
        assert LogEntry.from_dict(payload).raw_line == ''


class TestMalformedNginxInput:
    """Error and fallback branches, each reached by a specific bad input."""

    def test_dash_body_size_becomes_zero(self):
        """`-` in the body-size field is standard nginx for "nothing sent".

        The regex captures it as \\S+, so int('-') raises and the handler
        substitutes 0. This is real-world input, not a synthetic edge case.
        """
        line = ('1.2.3.4 - - [24/Mar/2023:17:07:41 +0000] '
                '"GET /health HTTP/1.1" 204 - "-" "curl/8.0"')

        entry = parse_nginx_line(line)

        assert entry is not None
        assert entry.size == 0
        assert entry.status == 204


class TestMalformedJsonInput:
    """The JSON parser is forgiving by design; these pin how forgiving."""

    def test_naive_timestamp_is_treated_as_utc(self):
        line = '{"remote_addr": "1.2.3.4", "url": "/a", "timestamp": "2023-03-24 17:07:41"}'

        entry = parse_json_line(line)

        assert entry is not None
        assert entry.timestamp.tzinfo is timezone.utc
        assert entry.timestamp.hour == 17

    def test_iso_z_timestamp_is_accepted(self):
        line = '{"ip": "1.2.3.4", "uri": "/a", "@timestamp": "2023-03-24T17:07:41Z"}'

        entry = parse_json_line(line)

        assert entry is not None
        assert entry.timestamp.year == 2023

    def test_missing_timestamp_falls_back_to_now(self):
        before = datetime.now(timezone.utc)

        entry = parse_json_line('{"remote_addr": "1.2.3.4", "url": "/a"}')

        assert entry is not None
        assert before <= entry.timestamp <= datetime.now(timezone.utc)

    def test_unparseable_status_falls_back_to_200(self):
        # Must be truthy and non-numeric: a falsy value short-circuits to the
        # literal 200 earlier, without reaching the conversion at all.
        entry = parse_json_line('{"remote_addr": "1.2.3.4", "status": "oops"}')

        assert entry is not None
        assert entry.status == 200

    def test_unparseable_size_falls_back_to_zero(self):
        entry = parse_json_line('{"remote_addr": "1.2.3.4", "body_bytes_sent": "big"}')

        assert entry is not None
        assert entry.size == 0


class TestCompressedAndAmbiguousFiles:
    def test_gzipped_log_is_read_transparently(self, tmp_path):
        import gzip

        path = tmp_path / "access.log.gz"
        with gzip.open(path, 'wt', encoding='utf-8') as handle:
            handle.write(
                '1.2.3.4 - - [24/Mar/2023:17:07:41 +0000] '
                '"GET /a HTTP/1.1" 200 100 "-" "curl/8.0"\n'
            )

        entries = list(parse_file(str(path)))

        assert len(entries) == 1
        assert entries[0].url == "/a"

    def test_detection_gives_up_after_ten_lines(self, tmp_path):
        path = tmp_path / "noise.log"
        path.write_text("this is not a log line\n" * 15, encoding='utf-8')

        assert detect_format(str(path)) == 'unknown'

    def test_a_line_starting_with_brace_that_is_not_json(self, tmp_path):
        path = tmp_path / "half.log"
        path.write_text('{not valid json\n', encoding='utf-8')

        assert detect_format(str(path)) == 'unknown'


class TestTimestampFallbacks:
    def test_nginx_timestamp_without_a_timezone(self):
        """Some log formats omit the `+0000`.

        The primary format string includes %z, so parsing fails and the
        fallback re-parses the date portion and assumes UTC.
        """
        line = ('1.2.3.4 - - [24/Mar/2023:17:07:41] '
                '"GET /a HTTP/1.1" 200 100 "-" "curl/8.0"')

        entry = parse_nginx_line(line)

        assert entry is not None
        assert entry.timestamp.tzinfo is timezone.utc
        assert entry.timestamp.hour == 17

    def test_nginx_timestamp_that_is_not_a_date_at_all(self):
        line = ('1.2.3.4 - - [not-a-date] '
                '"GET /a HTTP/1.1" 200 100 "-" "curl/8.0"')

        assert parse_nginx_line(line) is None

    def test_detection_skips_blank_leading_lines(self, tmp_path):
        path = tmp_path / "padded.log"
        path.write_text(
            '\n\n\n1.2.3.4 - - [24/Mar/2023:17:07:41 +0000] '
            '"GET /a HTTP/1.1" 200 100 "-" "curl/8.0"\n',
            encoding='utf-8',
        )

        assert detect_format(str(path)) == 'nginx'
