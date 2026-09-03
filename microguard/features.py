"""Feature extraction from log entries for bot detection.

Implements 19 features based on Nescio98 research + original plan:
- 5 timing features
- 4 behavioral features
- 3 header features
- 2 payload features
- 1 context feature
- 4 bonus features from Nescio98
"""

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from .parser import LogEntry


# Known bot user agent patterns (case-insensitive)
BOT_UA_PATTERNS = [
    r'bot', r'spider', r'crawler', r'scraper', r'curl', r'wget',
    r'python-requests', r'python-urllib', r'go-http-client',
    r'java/', r'perl', r'ruby', r'php/', r'node\.js',
    r'scrapy', r'headless', r'phantom', r'selenium', r'puppeteer',
    r'playwright', r'httpclient', r'okhttp', r'apache-httpclient',
    r'mechanize', r'beautifulsoup', r'lxml', r'http\.client',
    r'googlebot', r'bingbot', r'yspider', r'slurp', r'duckduckbot',
    r'facebot', r'facebookexternalhit', r'twitterbot', r'linkedinbot',
    r'bot\b', r'\bbot\b',
]

KNOWN_BROWSER_UA = [
    r'mozilla.*chrome', r'mozilla.*firefox', r'mozilla.*safari',
    r'mozilla.*edge', r'mozilla.*opera', r'mozilla.*msie',
    r'mozilla.*applewebkit', r'applewebkit.*safari',
    r'chrome/', r'firefox/', r'safari/',
]

BOT_UA_RE = re.compile('|'.join(BOT_UA_PATTERNS), re.IGNORECASE)
BROWSER_UA_RE = re.compile('|'.join(KNOWN_BROWSER_UA), re.IGNORECASE)


def _shannon_entropy(data: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not data:
        return 0.0
    
    freq = Counter(data)
    length = len(data)
    entropy = 0.0
    
    for count in freq.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    
    return entropy


def _coefficient_of_variation(values: List[float]) -> float:
    """Calculate coefficient of variation (std/mean)."""
    if len(values) < 2:
        return 0.0
    
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    std = math.sqrt(variance)
    
    return std / mean


def _url_depth(url: str) -> int:
    """Calculate URL path depth."""
    path = url.split('?')[0]  # Remove query string
    segments = [s for s in path.split('/') if s]
    return len(segments)


def _url_width(urls: List[str]) -> int:
    """Calculate URL space width (unique path branches at depth 1)."""
    branches = set()
    for url in urls:
        path = url.split('?')[0]
        segments = [s for s in path.split('/') if s]
        if segments:
            branches.add(segments[0])
    return len(branches)


class Session:
    """A group of requests from the same IP/client."""
    
    def __init__(self, ip: str, user_agent: str = ""):
        self.ip = ip
        self.user_agent = user_agent
        self.requests: List[LogEntry] = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
    
    def add_request(self, entry: LogEntry):
        self.requests.append(entry)
        if self.start_time is None or entry.timestamp < self.start_time:
            self.start_time = entry.timestamp
        if self.end_time is None or entry.timestamp > self.end_time:
            self.end_time = entry.timestamp
    
    @property
    def duration(self) -> float:
        """Session duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0
    
    @property
    def request_count(self) -> int:
        return len(self.requests)


def group_into_sessions(
    entries: List[LogEntry],
    timeout_minutes: int = 30
) -> List[Session]:
    """Group log entries into sessions by IP.
    
    A new session starts when:
    - A new IP appears
    - More than timeout_minutes pass between requests
    """
    all_sessions: List[Session] = []
    last_session_by_ip: Dict[str, Session] = {}
    
    for entry in sorted(entries, key=lambda e: e.timestamp):
        ip = entry.ip
        
        if ip in last_session_by_ip:
            session = last_session_by_ip[ip]
            # Check timeout
            if session.end_time:
                gap = (entry.timestamp - session.end_time).total_seconds()
                if gap > timeout_minutes * 60:
                    # New session for this IP
                    session = Session(ip, entry.user_agent)
                    all_sessions.append(session)
                    last_session_by_ip[ip] = session
            session.add_request(entry)
        else:
            session = Session(ip, entry.user_agent)
            session.add_request(entry)
            all_sessions.append(session)
            last_session_by_ip[ip] = session
    
    return all_sessions


def extract_features(session: Session) -> List[float]:
    """Extract 19 features from a session.
    
    Returns a list of 19 floats, ready for model input.
    Missing features (not available from logs) are padded to 0.
    """
    if session.request_count == 0:
        return [0.0] * 19
    
    entries = session.requests
    timestamps = [e.timestamp for e in entries]
    
    # === TIMING FEATURES (1-5) ===
    
    # 1. time_since_last_request (average gap between requests in seconds)
    gaps = []
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i-1]).total_seconds()
        gaps.append(gap)
    avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
    
    # 2. requests_per_minute_1m (count in last 60 seconds)
    if len(timestamps) >= 2:
        last_ts = timestamps[-1]
        count_1m = sum(1 for t in timestamps 
                      if (last_ts - t).total_seconds() <= 60)
    else:
        count_1m = 1
    
    # 3. requests_per_minute_5m (count in last 300 seconds)
    if len(timestamps) >= 2:
        last_ts = timestamps[-1]
        count_5m = sum(1 for t in timestamps 
                      if (last_ts - t).total_seconds() <= 300)
    else:
        count_5m = 1
    
    # 4. inter_request_time_cv (coefficient of variation)
    cv = _coefficient_of_variation(gaps) if gaps else 0.0
    
    # 5. time_since_session_start (session duration in seconds)
    session_duration = session.duration
    
    # === BEHAVIORAL FEATURES (6-9) ===
    
    # 6. endpoint_count (unique endpoints)
    urls = [e.url.split('?')[0] for e in entries]
    unique_urls = set(urls)
    endpoint_count = len(unique_urls)
    
    # 7. endpoint_sequence_entropy (Shannon entropy of URL sequence)
    url_freq = Counter(urls)
    total = len(urls)
    entropy = 0.0
    for count in url_freq.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    
    # 8. unique_endpoint_ratio
    unique_ratio = endpoint_count / len(entries) if entries else 0.0
    
    # 9. method_mismatch_count (POST to typically GET-only endpoints)
    method_mismatches = 0
    for e in entries:
        # POST to static resources is suspicious
        if e.method == 'POST' and any(e.url.endswith(ext) for ext in 
            ['.css', '.js', '.png', '.jpg', '.gif', '.ico', '.svg']):
            method_mismatches += 1
    
    # === HEADER FEATURES (10-12) ===
    
    # 10. header_consistency_score (0-1, how consistent are headers)
    # Based on User-Agent consistency across requests
    ua_variants = set(e.user_agent for e in entries)
    header_consistency = 1.0 / len(ua_variants) if ua_variants else 0.0
    
    # 11. has_accept_language (1 if present, 0 if not)
    # Heuristic: browsers always send this, bots often don't
    has_accept_lang = 0.0
    for e in entries:
        ua = e.user_agent.lower()
        if 'accept-language' in ua or BROWSER_UA_RE.search(ua):
            has_accept_lang = 1.0
            break
    
    # 12. ua_category (0=browser, 1=bot, 2=unknown)
    ua = session.user_agent.lower()
    if not ua or ua == '-':
        ua_category = 2.0
    elif BOT_UA_RE.search(ua):
        ua_category = 1.0
    elif BROWSER_UA_RE.search(ua):
        ua_category = 0.0
    else:
        ua_category = 2.0
    
    # === PAYLOAD FEATURES (13-14) ===
    
    # 13. payload_entropy (average entropy of request URLs)
    url_entropies = [_shannon_entropy(url) for url in urls]
    avg_payload_entropy = sum(url_entropies) / len(url_entropies) if url_entropies else 0.0
    
    # 14. field_fill_speed (NOT AVAILABLE IN LOGS — pad to 0)
    field_fill_speed = 0.0
    
    # === CONTEXT FEATURE (15) ===
    
    # 15. same_endpoint_hits (max hits to any single endpoint)
    url_counts = Counter(urls)
    same_endpoint_hits = max(url_counts.values()) if url_counts else 0
    
    # === BONUS FEATURES (16-19) from Nescio98 ===
    
    # 16. error_rate (% requests with status >= 400)
    error_count = sum(1 for e in entries if e.status >= 400)
    error_rate = error_count / len(entries) if entries else 0.0
    
    # 17. image_ratio (% image requests)
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp'}
    image_count = sum(1 for e in entries 
                     if any(e.url.lower().endswith(ext) for ext in image_extensions))
    image_ratio = image_count / len(entries) if entries else 0.0
    
    # 18. night_ratio (% requests between 2am-6am)
    night_count = sum(1 for e in entries 
                     if 2 <= e.timestamp.hour < 6)
    night_ratio = night_count / len(entries) if entries else 0.0
    
    # 19. max_sustained_click_rate (max HTML requests in 12s window)
    html_timestamps = sorted([e.timestamp for e in entries 
                             if e.url.endswith('.html') or e.url.endswith('/')])
    max_click_rate = 0.0
    if len(html_timestamps) >= 2:
        window = timedelta(seconds=12)
        for i, ts in enumerate(html_timestamps):
            count = sum(1 for t in html_timestamps[i:] if t - ts <= window)
            rate = count / 12.0  # clicks per second
            max_click_rate = max(max_click_rate, rate)
    
    return [
        # Timing (1-5)
        avg_gap,                    # 1. time_since_last_request
        float(count_1m),            # 2. requests_per_minute_1m
        float(count_5m),            # 3. requests_per_minute_5m
        cv,                         # 4. inter_request_time_cv
        session_duration,           # 5. time_since_session_start
        
        # Behavioral (6-9)
        float(endpoint_count),      # 6. endpoint_count
        entropy,                    # 7. endpoint_sequence_entropy
        unique_ratio,               # 8. unique_endpoint_ratio
        float(method_mismatches),   # 9. method_mismatch_count
        
        # Header (10-12)
        header_consistency,         # 10. header_consistency_score
        has_accept_lang,            # 11. has_accept_language
        ua_category,                # 12. ua_category
        
        # Payload (13-14)
        avg_payload_entropy,        # 13. payload_entropy
        field_fill_speed,           # 14. field_fill_speed (N/A in logs)
        
        # Context (15)
        float(same_endpoint_hits),  # 15. same_endpoint_hits
        
        # Bonus (16-19)
        error_rate,                 # 16. error_rate
        image_ratio,                # 17. image_ratio
        night_ratio,                # 18. night_ratio
        max_click_rate,             # 19. max_sustained_click_rate
    ]


# Feature names for display/debugging
FEATURE_NAMES = [
    'time_since_last_request',
    'requests_per_minute_1m',
    'requests_per_minute_5m',
    'inter_request_time_cv',
    'time_since_session_start',
    'endpoint_count',
    'endpoint_sequence_entropy',
    'unique_endpoint_ratio',
    'method_mismatch_count',
    'header_consistency_score',
    'has_accept_language',
    'ua_category',
    'payload_entropy',
    'field_fill_speed',
    'same_endpoint_hits',
    'error_rate',
    'image_ratio',
    'night_ratio',
    'max_sustained_click_rate',
]

assert len(FEATURE_NAMES) == 19, f"Expected 19 feature names, got {len(FEATURE_NAMES)}"
