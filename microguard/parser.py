"""Log file parser for Nginx combined and JSON structured formats."""

import gzip
import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

# Nginx combined log format regex
# Example: 192.168.1.1 - - [24/Mar/2023:17:07:41 +0000] "GET /products HTTP/1.1" 200 1234 "https://example.com" "Mozilla/5.0 ..."
NGINX_COMBINED_RE = re.compile(
    r'(?P<ip>\S+)\s+'           # client IP
    r'\S+\s+'                   # ident (usually -)
    r'(?P<authuser>\S+)\s+'     # auth user (usually -)
    r'\[(?P<timestamp>[^\]]+)\]\s+'  # timestamp
    r'"(?P<method>\S+)\s+'      # HTTP method
    r'(?P<url>\S+)\s+'          # request URL
    r'\S+"\s+'                  # HTTP version
    r'(?P<status>\d{3})\s+'     # status code
    r'(?P<size>\S+)\s+'         # response size (- for empty)
    r'"(?P<referer>[^"]*)"\s+'  # referer
    r'"(?P<user_agent>[^"]*)"'  # user agent
)

# Nginx timestamp format: 24/Mar/2023:17:07:41 +0000
NGINX_TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"


class LogEntry:
    """A single parsed log entry."""
    
    __slots__ = [
        'ip',
        'method',
        'raw_line',
        'referer',
        'size',
        'status',
        'timestamp',
        'url',
        'user_agent'
    ]
    
    def __init__(
        self,
        ip: str,
        timestamp: datetime,
        method: str,
        url: str,
        status: int,
        size: int,
        referer: str,
        user_agent: str,
        raw_line: str = ""
    ):
        self.ip = ip
        self.timestamp = timestamp
        self.method = method.upper()
        self.url = url
        self.status = status
        self.size = size
        self.referer = referer
        self.user_agent = user_agent
        self.raw_line = raw_line
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'ip': self.ip,
            'timestamp': self.timestamp.isoformat(),
            'method': self.method,
            'url': self.url,
            'status': self.status,
            'size': self.size,
            'referer': self.referer,
            'user_agent': self.user_agent,
        }
    
    def __repr__(self):
        return f"LogEntry({self.method} {self.url} {self.status} from {self.ip})"


def parse_nginx_line(line: str) -> LogEntry | None:
    """Parse a single Nginx combined log line."""
    line = line.strip()
    if not line:
        return None
    
    match = NGINX_COMBINED_RE.match(line)
    if not match:
        return None
    
    try:
        timestamp = datetime.strptime(match.group('timestamp'), NGINX_TIME_FMT)  # noqa: DTZ007 — NGINX_TIME_FMT includes %z, result is already tz-aware
    except ValueError:
        # Try without timezone
        try:
            timestamp = datetime.strptime(
                match.group('timestamp').split()[0],
                "%d/%b/%Y:%H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    
    try:
        size = int(match.group('size'))
    except (ValueError, TypeError):
        size = 0
    
    try:
        status = int(match.group('status'))
    except (ValueError, TypeError):
        return None
    
    return LogEntry(
        ip=match.group('ip'),
        timestamp=timestamp,
        method=match.group('method'),
        url=match.group('url'),
        status=status,
        size=size,
        referer=match.group('referer'),
        user_agent=match.group('user_agent'),
        raw_line=line,
    )


def parse_json_line(line: str) -> LogEntry | None:
    """Parse a single JSON log line."""
    line = line.strip()
    if not line:
        return None
    
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    
    # Handle various JSON log formats
    ip = data.get('remote_addr') or data.get('ip') or data.get('client_ip') or ''
    method = data.get('method') or data.get('request_method') or 'GET'
    url = data.get('url') or data.get('request_uri') or data.get('uri') or '/'
    status = data.get('status') or data.get('response_code') or 200
    size = data.get('body_bytes_sent') or data.get('bytes') or data.get('size') or 0
    referer = data.get('http_referer') or data.get('referer') or '-'
    user_agent = data.get('http_user_agent') or data.get('user_agent') or data.get('ua') or ''
    
    # Parse timestamp
    ts_str = data.get('time_local') or data.get('timestamp') or data.get('@timestamp') or ''
    timestamp = None
    for fmt in [
        "%d/%b/%Y:%H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]:
        try:
            timestamp = datetime.strptime(ts_str, fmt)  # noqa: DTZ007 — normalized to UTC below when naive
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    try:
        status = int(status)
    except (ValueError, TypeError):
        status = 200
    
    try:
        size = int(size)
    except (ValueError, TypeError):
        size = 0
    
    return LogEntry(
        ip=str(ip),
        timestamp=timestamp,
        method=str(method).upper(),
        url=str(url),
        status=status,
        size=size,
        referer=str(referer),
        user_agent=str(user_agent),
        raw_line=line,
    )


def _open_text(filepath: str):
    """Open a log file in text mode, transparently decompressing .gz files."""
    if filepath.endswith('.gz'):
        return gzip.open(filepath, 'rt', errors='replace')
    return open(filepath, 'r', errors='replace')


def detect_format(filepath: str) -> str:
    """Auto-detect log format by reading first few lines."""
    with _open_text(filepath) as f:
        for i, line in enumerate(f):
            if i >= 10:
                break
            line = line.strip()
            if not line:
                continue
            # Try JSON first
            if line.startswith('{'):
                try:
                    json.loads(line)
                    return 'json'
                except json.JSONDecodeError:
                    pass
            # Try Nginx combined
            if NGINX_COMBINED_RE.match(line):
                return 'nginx'
    return 'unknown'


def parse_file(filepath: str, fmt: str = 'auto') -> Iterator[LogEntry]:
    """Parse a log file, yielding LogEntry objects.
    
    Args:
        filepath: Path to the log file
        fmt: Format to use ('nginx', 'json', or 'auto' to detect)
    
    Yields:
        LogEntry objects for each valid log line
    """
    if fmt == 'auto':
        fmt = detect_format(filepath)
    
    parser = parse_json_line if fmt == 'json' else parse_nginx_line
    
    try:
        with _open_text(filepath) as f:
            for line in f:
                entry = parser(line)
                if entry is not None:
                    yield entry
    except FileNotFoundError:
        raise FileNotFoundError(f"Log file not found: {filepath}")


def parse_string(log_text: str, fmt: str = 'nginx') -> list:
    """Parse a log string, returning a list of LogEntry objects.
    
    Useful for testing and small inputs.
    """
    parser = parse_json_line if fmt == 'json' else parse_nginx_line
    results = []
    for line in log_text.strip().split('\n'):
        entry = parser(line)
        if entry is not None:
            results.append(entry)
    return results
