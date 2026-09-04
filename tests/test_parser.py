"""Tests for the log parser module."""


import pytest

from microguard.parser import (
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
        log_file.write_text(NGINX_HUMAN + "\n" + NGINX_BOT + "\n")
        fmt = detect_format(str(log_file))
        assert fmt == 'nginx'
    
    def test_detect_json(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(JSON_HUMAN + "\n" + JSON_BOT + "\n")
        fmt = detect_format(str(log_file))
        assert fmt == 'json'
    
    def test_detect_unknown(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("random text\nmore random text\n")
        fmt = detect_format(str(log_file))
        assert fmt == 'unknown'


class TestParseFile:
    """Tests for file parsing."""
    
    def test_parse_nginx_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(NGINX_HUMAN + "\n" + NGINX_BOT + "\n")
        
        entries = list(parse_file(str(log_file)))
        assert len(entries) == 2
        assert entries[0].ip == '192.168.1.100'
        assert entries[1].ip == '10.0.0.1'
    
    def test_parse_json_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text(JSON_HUMAN + "\n" + JSON_BOT + "\n")
        
        entries = list(parse_file(str(log_file), fmt='json'))
        assert len(entries) == 2
    
    def test_parse_missing_file(self):
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
