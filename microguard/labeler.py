"""Heuristic labeler for creating bot/human labels from log data.

Uses known bot signatures + timing patterns to create labels.
Labels are noisy but good enough for pre-training. Retrain on real data later.
"""

import re
from typing import List, Tuple

from .parser import LogEntry
from .features import Session, extract_features, BOT_UA_RE, BROWSER_UA_RE


# High-confidence bot user agent patterns
HIGH_CONFIDENCE_BOT_PATTERNS = [
    r'curl', r'wget', r'python-requests', r'python-urllib',
    r'go-http-client', r'java/', r'perl', r'ruby', r'php/',
    r'scrapy', r'headless', r'phantom', r'selenium', r'puppeteer',
    r'playwright', r'httpclient', r'okhttp', r'apache-httpclient',
    r'mechanize', r'beautifulsoup', r'lxml', r'http\.client',
    r'bot\b', r'\bbot\b', r'spider', r'crawler', r'scraper',
    r'Uptime-Kuma', r'HetrixTools', r'Pingdom', r'UptimeRobot',
    r'Nagios', r'Zabbix', r'Prometheus', r'Grafana',
    r'Semrush', r'Ahrefs', r'MozBot', r'MJ12bot',
    r'Googlebot', r'Bingbot', r'YandexBot', r'DuckDuckBot',
    r'Applebot', r'Bytespider', r'GPTBot', r'CCBot',
    r'facebookexternalhit', r'Twitterbot', r'LinkedInBot',
    r'Masscan', r'Nmap', r'ZmEu', r'nikto', r'sqlmap',
    r'Havij', r'w3af', r'OpenVAS', r'Nessus', r'Qualys',
]

HIGH_CONF_BOT_RE = re.compile('|'.join(HIGH_CONFIDENCE_BOT_PATTERNS), re.IGNORECASE)


def label_session(session: Session) -> Tuple[str, float, str]:
    """Label a session as 'bot' or 'human' with confidence.
    
    Returns:
        (label, confidence, reason)
        label: 'bot' or 'human'
        confidence: 0.0 to 1.0
        reason: human-readable explanation
    """
    if session.request_count == 0:
        return 'human', 0.5, 'empty session'
    
    entries = session.requests
    ua = session.user_agent.lower()
    urls = [e.url.split('?')[0] for e in entries]
    
    # === HIGH CONFIDENCE BOT SIGNALS (0.90-0.99) ===
    
    # 1. Known bot/monitoring user agent
    if HIGH_CONF_BOT_RE.search(ua):
        return 'bot', 0.95, f'known bot/monitoring UA: {session.user_agent[:50]}'
    
    # 2. Known vulnerability scanner patterns in URL
    scanner_patterns = ['/wp-admin', '/wp-login', '/phpmyadmin', '/.env',
                       '/config.json', '/admin/login', '/xmlrpc.php',
                       '/wp-content', '/wp-includes', '/cgi-bin']
    if any(any(p in url.lower() for p in scanner_patterns) for url in urls):
        return 'bot', 0.95, 'vulnerability scanner pattern detected'
    
    # 3. Extremely uniform timing (all requests within 1ms of each other)
    if session.request_count >= 5:
        timestamps = sorted([e.timestamp for e in entries])
        gaps = [(timestamps[i+1] - timestamps[i]).total_seconds() 
                for i in range(len(timestamps)-1)]
        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            max_gap = max(gaps)
            min_gap = min(gaps)
            # All gaps nearly identical = bot
            if avg_gap > 0 and (max_gap - min_gap) / avg_gap < 0.05:
                return 'bot', 0.90, f'uniform timing (avg {avg_gap:.3f}s, near-zero variance)'
    
    # 4. HTTP/1.0 only (no modern browser uses this)
    if all(e.raw_line and 'HTTP/1.0' in e.raw_line for e in entries):
        if session.request_count > 5:
            return 'bot', 0.90, 'all requests use HTTP/1.0 (not a modern browser)'
    
    # === MEDIUM CONFIDENCE BOT SIGNALS (0.70-0.89) ===
    
    # 5. Very high request rate (>100 requests in session)
    if session.request_count > 100:
        return 'bot', 0.85, f'extremely high request count: {session.request_count}'
    
    # 6. All requests to same endpoint (scraper pattern)
    unique_urls = set(urls)
    if len(unique_urls) == 1 and session.request_count > 10:
        return 'bot', 0.80, f'all {session.request_count} requests to same endpoint: {urls[0]}'
    
    # 7. High request rate (>50 req/min sustained)
    if session.duration > 0:
        rate = session.request_count / (session.duration / 60.0)
        if rate > 50:
            return 'bot', 0.75, f'high request rate: {rate:.1f} req/min'
    
    # 8. No referrer on all requests (direct API hits)
    no_referrer = sum(1 for e in entries if e.referer in ('-', '', 'none'))
    if no_referrer == len(entries) and session.request_count > 20:
        return 'bot', 0.70, f'no referrer on all {session.request_count} requests'
    
    # 9. High error rate (>50% 4xx/5xx responses)
    errors = sum(1 for e in entries if e.status >= 400)
    if errors / len(entries) > 0.5 and session.request_count > 10:
        return 'bot', 0.70, f'high error rate: {errors}/{len(entries)} failed requests'
    
    # 10. Repeated same endpoint with different parameters (API abuse)
    from collections import Counter
    path_counts = Counter(urls)
    most_common_count = path_counts.most_common(1)[0][1] if path_counts else 0
    if most_common_count > 20 and most_common_count / len(entries) > 0.7:
        return 'bot', 0.70, f'repeated endpoint hit {most_common_count} times'
    
    # === LOW CONFIDENCE BOT SIGNALS (0.55-0.69) ===
    
    # 11. Unknown user agent (not a known browser)
    if ua and ua != '-' and not BROWSER_UA_RE.search(ua):
        if session.request_count > 5:
            return 'bot', 0.60, f'unknown user-agent: {session.user_agent[:50]}'
    
    # 12. Very short session with many requests (< 5 seconds, > 20 requests)
    if session.duration < 5.0 and session.request_count > 20:
        return 'bot', 0.65, f'{session.request_count} requests in {session.duration:.1f}s'
    
    # 13. Night-time activity (2am-6am) with high volume
    night_count = sum(1 for e in entries if 2 <= e.timestamp.hour < 6)
    if night_count / len(entries) > 0.5 and session.request_count > 30:
        return 'bot', 0.60, f'mostly night-time activity ({night_count}/{len(entries)} requests)'
    
    # === HUMAN SIGNALS (0.55-0.75) ===
    
    # 14. Known browser user agent with normal behavior
    if BROWSER_UA_RE.search(ua):
        if session.request_count < 50 and session.duration > 30:
            return 'human', 0.75, f'known browser, reasonable session ({session.request_count} req, {session.duration:.0f}s)'
    
    # 15. Variable timing pattern (high CV)
    if session.request_count >= 3:
        timestamps = sorted([e.timestamp for e in entries])
        gaps = [(timestamps[i+1] - timestamps[i]).total_seconds() 
                for i in range(len(timestamps)-1)]
        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            max_gap = max(gaps)
            if avg_gap > 0 and max_gap / avg_gap > 3.0:
                return 'human', 0.70, f'variable timing (max/avg ratio: {max_gap/avg_gap:.1f})'
    
    # 16. Multiple different endpoints explored (browsing pattern)
    if len(unique_urls) >= 5 and session.request_count >= 5:
        return 'human', 0.65, f'exploring {len(unique_urls)} different endpoints'
    
    # 17. Has referer chain (natural navigation)
    has_referer = sum(1 for e in entries if e.referer not in ('-', '', 'none'))
    if has_referer > len(entries) * 0.5 and session.request_count >= 3:
        return 'human', 0.60, f'natural navigation with {has_referer} referrers'
    
    # === DEFAULT ===
    
    # If we can't tell, lean toward human (avoid false positives)
    return 'human', 0.50, 'no strong signals either way'


def label_entries(
    entries: List[LogEntry],
    timeout_minutes: int = 30
) -> List[Tuple[Session, str, float, str]]:
    """Label all sessions in a list of log entries.
    
    Returns:
        List of (session, label, confidence, reason) tuples
    """
    from .features import group_into_sessions
    
    sessions = group_into_sessions(entries, timeout_minutes)
    results = []
    
    for session in sessions:
        label, confidence, reason = label_session(session)
        results.append((session, label, confidence, reason))
    
    return results
