"""HTTP scanner for live URL probing.

Makes requests to a target URL and extracts bot-detection features
from the response. No external dependencies — uses stdlib only.
"""

import json
import math
import re
import ssl
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import Counter


@dataclass
class ProbeResult:
    """Result of probing a single URL."""
    url: str
    status_code: int = 0
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    timing: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None
    
    # Extracted features (populated by extract_probe_features)
    features: Dict[str, float] = field(default_factory=dict)
    
    @property
    def body_text(self) -> str:
        try:
            return self.body.decode('utf-8', errors='replace')
        except Exception:
            return ""


def _shannon_entropy(data) -> float:
    """Calculate Shannon entropy of data."""
    if not data:
        return 0.0
    if isinstance(data, str):
        data = data.encode('utf-8', errors='replace')
    counts = Counter(data)
    length = len(data)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _timing_pattern_consistency(timings: List[float]) -> float:
    """Measure consistency of timing pattern (low CV = bot-like)."""
    if len(timings) < 2:
        return 0.0
    mean = sum(timings) / len(timings)
    if mean == 0:
        return 0.0
    variance = sum((t - mean) ** 2 for t in timings) / len(timings)
    std = math.sqrt(variance)
    cv = std / mean if mean > 0 else 0.0
    return cv


def probe_url(
    url: str,
    method: str = "GET",
    timeout: float = 10.0,
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    follow_redirects: bool = True,
    verify_ssl: bool = True,
    headers: Optional[Dict[str, str]] = None,
) -> ProbeResult:
    """Probe a live URL and capture response details.
    
    Args:
        url: Target URL to probe
        method: HTTP method (GET, POST, etc.)
        timeout: Request timeout in seconds
        user_agent: User-Agent header to send
        follow_redirects: Whether to follow HTTP redirects
        verify_ssl: Whether to verify SSL certificates
        headers: Additional headers to send
    
    Returns:
        ProbeResult with response data and timing
    """
    # Ensure URL has scheme
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Build request headers
    req_headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'identity',
        'Connection': 'close',
    }
    if headers:
        req_headers.update(headers)
    
    req = urllib.request.Request(url, method=method, headers=req_headers)
    
    # SSL context
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = ssl.create_default_context()
    
    # Build opener with handlers
    handlers = []
    
    # HTTPS handler with SSL context
    https_handler = urllib.request.HTTPSHandler(context=ctx)
    handlers.append(https_handler)
    
    if not follow_redirects:
        # Disable redirect following
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise urllib.error.HTTPError(newurl, code, msg, headers, fp)
        handlers.append(NoRedirect())
    
    opener = urllib.request.build_opener(*handlers)
    
    result = ProbeResult(url=url)
    
    try:
        # Measure timing
        t_start = time.time()
        try:
            response = opener.open(req, timeout=timeout)
            t_connected = time.time()
            
            # Read response body
            body = response.read(1024 * 1024)  # Max 1MB
            t_complete = time.time()
            
            result.status_code = response.status
            result.headers = dict(response.headers)
            result.body = body
            result.timing = {
                'dns': t_connected - t_start,  # Approximate
                'connect': 0.0,  # Not easily separable with urllib
                'ttfb': t_connected - t_start,
                'total': t_complete - t_start,
                'transfer': t_complete - t_connected,
            }
        except urllib.error.HTTPError as e:
            t_error = time.time()
            result.status_code = e.code
            result.headers = dict(e.headers) if e.headers else {}
            result.timing = {
                'dns': 0.0,
                'connect': 0.0,
                'ttfb': 0.0,
                'total': t_error - t_start,
                'transfer': 0.0,
            }
        except urllib.error.URLError as e:
            result.error = str(e.reason)
            result.timing = {'total': time.time() - t_start}
    
    except Exception as e:
        result.error = str(e)
        result.timing = {'total': time.time() - t_start}
    
    return result


def probe_url_multiple(
    url: str,
    count: int = 1,
    delay: float = 0.0,
    method: str = "GET",
    timeout: float = 10.0,
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    randomize_ua: bool = False,
) -> List[ProbeResult]:
    """Probe a URL multiple times to detect timing patterns.
    
    Args:
        url: Target URL to probe
        count: Number of probes to send
        delay: Delay between probes in seconds
        method: HTTP method
        timeout: Request timeout
        user_agent: User-Agent header
        randomize_ua: Rotate through common browser UAs
    
    Returns:
        List of ProbeResult objects
    """
    user_agents = [
        user_agent,
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
    
    results = []
    for i in range(count):
        ua = user_agents[i % len(user_agents)] if randomize_ua else user_agent
        result = probe_url(url, method=method, timeout=timeout, user_agent=ua)
        results.append(result)
        
        if delay > 0 and i < count - 1:
            time.sleep(delay)
    
    return results


def extract_probe_features(results: List[ProbeResult]) -> Dict[str, float]:
    """Extract bot-detection features from probe results.
    
    Features extracted:
    - response_time: Total response time (seconds)
    - ttfb: Time to first byte (seconds)
    - status_code: HTTP status code (normalized 0-1)
    - header_count: Number of response headers (normalized)
    - body_entropy: Shannon entropy of response body
    - has_server_header: 1.0 if Server header present
    - has_x_powered_by: 1.0 if X-Powered-By header present
    - has_content_security_policy: 1.0 if CSP header present
    - has_x_frame_options: 1.0 if X-Frame-Options present
    - has_strict_transport: 1.0 if HSTS header present
    - timing_cv: Coefficient of variation of response times
    - body_length: Response body length (normalized)
    - redirect_count: Number of redirects followed
    - has_accept_language: 1.0 if response suggests content negotiation
    
    Args:
        results: List of ProbeResult objects (1 for single, N for multi)
    
    Returns:
        Dictionary of feature name -> value
    """
    if not results:
        return {}
    
    first = results[0]
    features = {}
    
    # --- Timing features ---
    features['response_time'] = first.timing.get('total', 0.0)
    features['ttfb'] = first.timing.get('ttfb', 0.0)
    
    # Timing consistency (multiple probes)
    if len(results) > 1:
        timings = [r.timing.get('total', 0.0) for r in results]
        features['timing_cv'] = _timing_pattern_consistency(timings)
    else:
        features['timing_cv'] = 0.0
    
    # --- Response features ---
    features['status_code'] = first.status_code / 1000.0  # Normalize to 0-1
    features['header_count'] = min(len(first.headers) / 20.0, 1.0)  # Normalize
    features['body_entropy'] = _shannon_entropy(first.body)
    features['body_length'] = min(len(first.body) / (1024 * 100), 1.0)  # Normalize to 100KB
    
    # --- Security header detection ---
    headers_lower = {k.lower(): v for k, v in first.headers.items()}
    
    features['has_server_header'] = 1.0 if 'server' in headers_lower else 0.0
    features['has_x_powered_by'] = 1.0 if 'x-powered-by' in headers_lower else 0.0
    features['has_content_security_policy'] = 1.0 if 'content-security-policy' in headers_lower else 0.0
    features['has_x_frame_options'] = 1.0 if 'x-frame-options' in headers_lower else 0.0
    features['has_strict_transport'] = 1.0 if 'strict-transport-security' in headers_lower else 0.0
    
    # --- Content features ---
    body_text = first.body_text.lower()
    features['has_accept_language'] = 1.0 if 'lang=' in body_text or 'language' in body_text else 0.0
    
    # --- Redirect detection ---
    redirect_count = 0
    for r in results:
        if r.status_code in (301, 302, 303, 307, 308):
            redirect_count += 1
    features['redirect_count'] = min(redirect_count / 5.0, 1.0)  # Normalize
    
    # --- Bot signal features ---
    # Check for common bot detection headers
    server = headers_lower.get('server', '').lower()
    powered_by = headers_lower.get('x-powered-by', '').lower()
    
    # Bot-friendly servers (Cloudflare, etc.) tend to block bots
    features['server_bot_score'] = 0.0
    if 'cloudflare' in server or 'akamai' in server:
        features['server_bot_score'] = 0.3  # CDN might be blocking bots
    elif 'apache' in server or 'nginx' in server:
        features['server_bot_score'] = 0.1  # Raw server, less protection
    
    # Check for rate limiting headers
    features['has_rate_limit'] = 1.0 if 'x-ratelimit' in str(headers_lower) or 'retry-after' in headers_lower else 0.0
    
    return features


def probe_and_analyze(
    url: str,
    model=None,
    count: int = 1,
    delay: float = 0.0,
    threshold: float = 0.7,
    verbose: bool = False,
) -> Dict:
    """Probe a URL and analyze for bot-detection signals.
    
    Args:
        url: Target URL to probe
        model: Optional BotDetector model for ML scoring
        count: Number of probes (more = better timing analysis)
        delay: Delay between probes
        threshold: Bot score threshold
        verbose: Print progress to stderr
    
    Returns:
        Dictionary with probe results and analysis
    """
    import sys
    
    if verbose:
        print(f"🔍 Probing {url}...", file=sys.stderr, flush=True)
    
    # Run probes
    results = probe_url_multiple(url, count=count, delay=delay)
    
    if verbose:
        for i, r in enumerate(results):
            status = f"HTTP {r.status_code}" if r.status_code else f"Error: {r.error}"
            print(f"   Probe {i+1}/{count}: {status} ({r.timing.get('total', 0):.3f}s)", file=sys.stderr, flush=True)
    
    # Extract features
    features = extract_probe_features(results)
    
    # Analyze with heuristic rules
    heuristic_score, heuristic_reason = _analyze_probes(results, features)
    
    # Analyze with ML model if available
    model_score = 0.0
    if model is not None:
        # Convert probe features to model-compatible vector
        feature_vector = _probe_features_to_vector(features)
        model_score = model.predict(feature_vector)
    
    # Combine scores (60% model, 40% heuristic)
    if model is not None:
        combined_score = 0.6 * model_score + 0.4 * heuristic_score
    else:
        combined_score = heuristic_score
    
    # Classify
    is_bot = combined_score >= threshold
    label = 'bot' if is_bot else 'human'
    
    return {
        'url': url,
        'probes': count,
        'features': features,
        'heuristic_score': heuristic_score,
        'heuristic_reason': heuristic_reason,
        'model_score': model_score,
        'combined_score': combined_score,
        'label': label,
        'threshold': threshold,
        'timing': results[0].timing if results else {},
        'status_code': results[0].status_code if results else 0,
        'headers': results[0].headers if results else {},
        'body_preview': results[0].body_text[:500] if results else '',
    }


def _analyze_probes(
    results: List[ProbeResult],
    features: Dict[str, float],
) -> Tuple[float, str]:
    """Analyze probe results with heuristic rules.
    
    Returns:
        (score, reason) tuple
    """
    if not results:
        return 0.5, 'no probe results'
    
    first = results[0]
    
    # Rule 1: Connection error (might be blocking bots)
    if first.error:
        return 0.6, f'connection error: {first.error[:50]}'
    
    # Rule 2: Very fast response (< 50ms) = likely cached or CDN
    total_time = first.timing.get('total', 0)
    if total_time < 0.05:
        return 0.3, f'extremely fast response ({total_time:.3f}s) — likely cached'
    
    # Rule 3: Very slow response (> 5s) = might be rate limiting
    if total_time > 5.0:
        return 0.5, f'slow response ({total_time:.3f}s) — possible rate limiting'
    
    # Rule 4: 429 Too Many Requests = rate limited
    if first.status_code == 429:
        return 0.8, 'rate limited (HTTP 429)'
    
    # Rule 5: 403 Forbidden = blocked
    if first.status_code == 403:
        return 0.7, 'forbidden (HTTP 403) — might be blocking'
    
    # Rule 6: 5xx Error = server issue
    if first.status_code >= 500:
        return 0.5, f'server error (HTTP {first.status_code})'
    
    # Rule 7: Security headers present = well-protected site
    headers_lower = {k.lower(): v for k, v in first.headers.items()}
    security_headers = [
        'content-security-policy',
        'x-frame-options',
        'strict-transport-security',
        'x-content-type-options',
    ]
    security_count = sum(1 for h in security_headers if h in headers_lower)
    if security_count >= 3:
        return 0.2, f'well-protected site ({security_count} security headers)'
    
    # Rule 8: Timing consistency across probes (bot signal)
    if len(results) > 2:
        cv = features.get('timing_cv', 0)
        if cv < 0.05 and cv > 0:
            return 0.7, f'very consistent timing (CV={cv:.3f}) — likely automated'
    
    # Rule 9: Very low body entropy (empty or minimal response)
    body_entropy = features.get('body_entropy', 0)
    if body_entropy < 1.0 and len(first.body) > 0:
        return 0.4, f'low response entropy ({body_entropy:.2f})'
    
    # Rule 10: High body entropy (random/encrypted content)
    if body_entropy > 7.5:
        return 0.3, f'high response entropy ({body_entropy:.2f}) — likely encrypted/compressed'
    
    # Default: no strong signals
    return 0.3, 'no strong bot signals detected'


def format_probe_report(result: Dict) -> str:
    """Format probe result for terminal output.
    
    Args:
        result: Dictionary from probe_and_analyze()
    
    Returns:
        Formatted string for terminal display
    """
    lines = []
    
    # Header
    lines.append("")
    lines.append("  Microguard URL Probe Report")
    lines.append("  ───────────────────────────")
    lines.append("")
    
    # Target info
    lines.append(f"  🌐 Target:    {result['url']}")
    lines.append(f"  📡 Status:    HTTP {result['status_code']}")
    lines.append(f"  ⏱️  Response:  {result['timing'].get('total', 0):.3f}s")
    lines.append(f"  🔍 Probes:    {result['probes']}")
    lines.append("")
    
    # Score
    score = result['combined_score']
    label = result['label']
    
    if score > 0.7:
        score_color = '\033[91m'  # Red
        icon = '🚨'
    elif score > 0.3:
        score_color = '\033[93m'  # Yellow
        icon = '⚠️ '
    else:
        score_color = '\033[92m'  # Green
        icon = '✅'
    
    reset = '\033[0m'
    lines.append(f"  {icon} Bot Score: {score_color}{score:.2f}{reset} ({label.upper()})")
    lines.append(f"  📊 Threshold: {result['threshold']:.2f}")
    lines.append("")
    
    # Analysis breakdown
    lines.append("  Analysis:")
    lines.append(f"    Heuristic:  {result['heuristic_score']:.2f} — {result['heuristic_reason']}")
    if result.get('model_score', 0) > 0:
        lines.append(f"    ML Model:   {result['model_score']:.2f}")
    lines.append("")
    
    # Key features
    features = result.get('features', {})
    if features:
        lines.append("  Key Features:")
        lines.append(f"    Response time:    {features.get('response_time', 0):.3f}s")
        lines.append(f"    TTFB:             {features.get('ttfb', 0):.3f}s")
        lines.append(f"    Body entropy:     {features.get('body_entropy', 0):.2f}")
        lines.append(f"    Body length:      {features.get('body_length', 0) * 100:.0f}KB (normalized)")
        lines.append(f"    Timing CV:        {features.get('timing_cv', 0):.3f}")
        lines.append(f"    Security headers: {int(features.get('has_content_security_policy', 0)) + int(features.get('has_x_frame_options', 0)) + int(features.get('has_strict_transport', 0))}")
        lines.append("")
    
    # Headers
    headers = result.get('headers', {})
    if headers:
        lines.append("  Response Headers:")
        for k, v in sorted(headers.items()):
            if len(v) > 60:
                v = v[:57] + '...'
            lines.append(f"    {k}: {v}")
        lines.append("")
    
    # Body preview
    body = result.get('body_preview', '')
    if body:
        lines.append("  Body Preview (first 200 chars):")
        preview = body[:200].replace('\n', ' ').replace('\r', '')
        lines.append(f"    {preview}")
        lines.append("")
    
    return '\n'.join(lines)


def format_probe_html(result: Dict) -> str:
    """Format probe result as HTML report.
    
    Args:
        result: Dictionary from probe_and_analyze()
    
    Returns:
        Complete HTML document string
    """
    score = result['combined_score']
    label = result['label']
    
    if score > 0.7:
        status_color = '#ef4444'
        status_text = 'BOT DETECTED'
    elif score > 0.3:
        status_color = '#f59e0b'
        status_text = 'SUSPICIOUS'
    else:
        status_color = '#10b981'
        status_text = 'LIKELY HUMAN'
    
    features = result.get('features', {})
    headers = result.get('headers', {})
    
    # Build features list
    features_html = ''
    feature_display = [
        ('response_time', 'Response Time', 's'),
        ('ttfb', 'Time to First Byte', 's'),
        ('timing_cv', 'Timing Consistency', ''),
        ('body_entropy', 'Body Entropy', ''),
        ('body_length', 'Body Size', 'KB'),
        ('header_count', 'Header Count', ''),
        ('has_content_security_policy', 'CSP Header', ''),
        ('has_x_frame_options', 'X-Frame-Options', ''),
        ('has_strict_transport', 'HSTS Header', ''),
    ]
    
    for key, name, unit in feature_display:
        val = features.get(key, 0)
        if 'has_' in key:
            val_str = '✅ Yes' if val > 0.5 else '❌ No'
        elif 'length' in key:
            val_str = f'{val * 100:.0f}{unit}'
        else:
            val_str = f'{val:.3f}{unit}'
        features_html += f'<tr><td>{name}</td><td class="mono">{val_str}</td></tr>\n'
    
    # Build headers list
    headers_html = ''
    for k, v in sorted(headers.items()):
        if len(v) > 80:
            v = v[:77] + '...'
        headers_html += f'<tr><td class="mono">{k}</td><td>{v}</td></tr>\n'
    
    body_preview = result.get('body_preview', '')[:500]
    if body_preview:
        body_preview = body_preview.replace('<', '&lt;').replace('>', '&gt;')
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Microguard Probe — {result['url']}</title>
    <style>
        :root {{
            --bg: #0f172a;
            --card: #1e293b;
            --card-border: #334155;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --green: #10b981;
            --yellow: #f59e0b;
            --red: #ef4444;
            --blue: #3b82f6;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2rem;
        }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        .header {{ text-align: center; margin-bottom: 2rem; padding-bottom: 2rem; border-bottom: 1px solid var(--card-border); }}
        .header h1 {{ font-size: 2rem; margin-bottom: 0.5rem; }}
        .header h1 span {{ color: var(--blue); }}
        .header .url {{ color: var(--text-muted); font-family: monospace; font-size: 0.9rem; word-break: break-all; }}
        .score-card {{
            background: var(--card);
            border: 2px solid {status_color};
            border-radius: 12px;
            padding: 2rem;
            text-align: center;
            margin-bottom: 2rem;
        }}
        .score-card .label {{ font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-muted); margin-bottom: 0.5rem; }}
        .score-card .value {{ font-size: 3rem; font-weight: 700; color: {status_color}; }}
        .score-card .status {{ font-size: 1.2rem; font-weight: 600; color: {status_color}; margin-top: 0.5rem; }}
        .info-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
        .info-item {{ background: var(--card); border: 1px solid var(--card-border); border-radius: 8px; padding: 1rem; text-align: center; }}
        .info-item .value {{ font-size: 1.5rem; font-weight: 600; }}
        .info-item .label {{ font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; }}
        .section {{ background: var(--card); border: 1px solid var(--card-border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; }}
        .section h2 {{ font-size: 1rem; margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--card-border); }}
        .analysis {{ padding: 1rem; background: #0f172a; border-radius: 8px; }}
        .analysis .label {{ color: var(--text-muted); font-size: 0.85rem; }}
        .analysis .value {{ font-weight: 600; }}
        .analysis .reason {{ color: var(--text-muted); font-size: 0.9rem; margin-top: 0.25rem; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
        th {{ text-align: left; padding: 0.5rem; color: var(--text-muted); font-size: 0.75rem; text-transform: uppercase; }}
        td {{ padding: 0.5rem; border-top: 1px solid var(--card-border); }}
        .mono {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.85rem; }}
        .footer {{ text-align: center; color: var(--text-muted); font-size: 0.8rem; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--card-border); }}
        .footer a {{ color: var(--blue); text-decoration: none; }}
        @media print {{
            body {{ background: white; color: #1e293b; }}
            .card {{ background: #f8fafc; border-color: #e2e8f0; }}
            .score-card {{ background: #f8fafc; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 <span>Microguard</span> URL Probe</h1>
            <div class="url">{result['url']}</div>
        </div>
        
        <div class="score-card">
            <div class="label">Bot Detection Score</div>
            <div class="value">{score:.2f}</div>
            <div class="status">{status_text}</div>
        </div>
        
        <div class="info-grid">
            <div class="info-item">
                <div class="value">HTTP {result['status_code']}</div>
                <div class="label">Status</div>
            </div>
            <div class="info-item">
                <div class="value">{result['timing'].get('total', 0):.3f}s</div>
                <div class="label">Response Time</div>
            </div>
            <div class="info-item">
                <div class="value">{result['probes']}</div>
                <div class="label">Probes Sent</div>
            </div>
            <div class="info-item">
                <div class="value">{result['threshold']:.0%}</div>
                <div class="label">Threshold</div>
            </div>
        </div>
        
        <div class="section">
            <h2>Analysis</h2>
            <div class="analysis">
                <div class="label">Heuristic Score</div>
                <div class="value">{result['heuristic_score']:.2f}</div>
                <div class="reason">{result['heuristic_reason']}</div>
            </div>
            {f'<div class="analysis" style="margin-top: 1rem"><div class="label">ML Model Score</div><div class="value">{result["model_score"]:.2f}</div></div>' if result.get('model_score', 0) > 0 else ''}
        </div>
        
        <div class="section">
            <h2>Extracted Features</h2>
            <table>
                <thead><tr><th>Feature</th><th>Value</th></tr></thead>
                <tbody>
                    {features_html}
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <h2>Response Headers</h2>
            <table>
                <thead><tr><th>Header</th><th>Value</th></tr></thead>
                <tbody>
                    {headers_html if headers_html else '<tr><td colspan="2" style="color: var(--text-muted)">No headers captured</td></tr>'}
                </tbody>
            </table>
        </div>
        
        {f'<div class="section"><h2>Body Preview</h2><pre class="mono" style="white-space: pre-wrap; word-break: break-all; font-size: 0.8rem; color: var(--text-muted);">{body_preview}</pre></div>' if body_preview else ''}
        
        <div class="footer">
            <p>Powered by <a href="https://github.com/karpathy/micrograd">micrograd</a> · Microguard v0.1.0</p>
        </div>
    </div>
</body>
</html>"""
    
    return html


def _probe_features_to_vector(features: Dict[str, float]) -> List[float]:
    """Convert probe features dict to a vector for the ML model.
    
    Maps probe features to the 19-feature model input format.
    Features not available from live probing are set to 0.
    """
    # Map available probe features to model's expected input
    return [
        features.get('response_time', 0.0),      # time_since_last_request
        features.get('ttfb', 0.0),                # requests_per_minute_1m
        features.get('timing_cv', 0.0),           # requests_per_minute_5m
        features.get('timing_cv', 0.0),           # inter_request_time_cv
        features.get('response_time', 0.0),       # time_since_session_start
        0.0,                                       # endpoint_count
        0.0,                                       # endpoint_sequence_entropy
        0.0,                                       # unique_endpoint_ratio
        0.0,                                       # method_mismatch_count
        features.get('header_count', 0.0),        # header_consistency_score
        features.get('has_accept_language', 0.0), # has_accept_language
        0.0,                                       # ua_category
        features.get('body_entropy', 0.0),        # payload_entropy
        0.0,                                       # field_fill_speed
        0.0,                                       # same_endpoint_hits
        features.get('status_code', 0.0),         # error_rate
        0.0,                                       # image_ratio
        0.0,                                       # night_ratio
        features.get('body_length', 0.0),         # max_sustained_click_rate
    ]
