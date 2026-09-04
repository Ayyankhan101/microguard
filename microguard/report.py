"""Report formatter for bot detection results.

Supports terminal table and JSON output formats.
"""

import json
from typing import Any


def _colorize(text: str, color: str) -> str:
    """Apply ANSI color to text."""
    colors = {
        'red': '\033[91m',
        'green': '\033[92m',
        'yellow': '\033[93m',
        'blue': '\033[94m',
        'cyan': '\033[96m',
        'bold': '\033[1m',
        'reset': '\033[0m',
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def score_label(score: float) -> str:
    """Return a human-readable risk label for a bot score.
    
    0.00-0.30 = SAFE
    0.31-0.59 = LOW
    0.60-0.79 = WARNING  
    0.80-1.00 = DANGER
    """
    if score <= 0.30:
        return 'SAFE'
    elif score <= 0.59:
        return 'LOW'
    elif score <= 0.79:
        return 'WARNING'
    else:
        return 'DANGER'


def score_label_color(score: float) -> str:
    """Return ANSI color name for a score label."""
    if score <= 0.30:
        return 'green'
    elif score <= 0.59:
        return 'blue'
    elif score <= 0.79:
        return 'yellow'
    else:
        return 'red'


def _score_bar(score: float, width: int = 10) -> str:
    """Create a colored ASCII score bar."""
    filled = int(score * width)
    empty = width - filled
    if score > 0.7:
        bar_char = _colorize('█', 'red')
    elif score > 0.3:
        bar_char = _colorize('█', 'yellow')
    else:
        bar_char = _colorize('█', 'green')
    return bar_char * filled + '░' * empty


def format_terminal(results: dict[str, Any]) -> str:
    """Format results as a terminal-friendly table.
    
    Args:
        results: Dictionary with scan results including:
            - total_sessions: int
            - bot_count: int
            - human_count: int
            - bot_rate: float
            - sessions: list of session results
            - summary: dict with aggregate stats
    """
    lines = []

    # Header
    lines.append("")
    lines.append(_colorize("  Microguard Bot Traffic Report", "bold"))
    lines.append(_colorize("  ─────────────────────────────", "cyan"))
    lines.append("")

    if results.get('error'):
        lines.append(_colorize(f"  ⚠️  {results['error']}", "yellow"))
        lines.append("     Check that the log file matches the expected format (nginx combined or JSON) and isn't empty.")
        lines.append("")
        return '\n'.join(lines)

    # Summary stats
    total = results.get('total_sessions', 0)
    bots = results.get('bot_count', 0)
    humans = results.get('human_count', 0)
    bot_rate = results.get('bot_rate', 0.0)
    
    # Status color based on bot rate
    if bot_rate < 0.1:
        status_color = 'green'
        status_icon = '✅'
        status_label = 'HEALTHY'
    elif bot_rate < 0.3:
        status_color = 'yellow'
        status_icon = '⚠️ '
        status_label = 'WARNING'
    else:
        status_color = 'red'
        status_icon = '🚨'
        status_label = 'CRITICAL'
    
    lines.append(f"  {status_icon} Bot Traffic: {_colorize(f'{bot_rate:.1%}', status_color)}  {_colorize(status_label, status_color)}")
    lines.append("")
    lines.append(f"  Total sessions:  {total}")
    lines.append(f"  Human sessions:  {_colorize(str(humans), 'green')}")
    lines.append(f"  Bot sessions:    {_colorize(str(bots), 'red')}")
    lines.append("")
    
    # Visual bot rate bar
    lines.append(f"  {_score_bar(bot_rate)}  {bot_rate:.1%}")
    lines.append("")
    
    # Top suspicious sessions
    sessions = results.get('sessions', [])
    if sessions:
        # Sort by bot score descending
        sorted_sessions = sorted(sessions, key=lambda s: s.get('score', 0), reverse=True)
        top_n = sorted_sessions[:10]
        
        if top_n:
            lines.append(_colorize("  Top Suspicious Sessions:", "bold"))
            lines.append("  ────────────────────────")
            lines.append("")
            
            # Table header
            lines.append(
                f"  {'IP':<18} {'Score':>5} {'Bar':<12} {'Risk':<9} {'Label':<7} {'Reqs':>5} "
                f"{'Dur':>6} {'Heuristic Rule'}"
            )
            lines.append("  " + "─" * 85)
            
            for s in top_n:
                ip = s.get('ip', '?')[:18]
                score = s.get('score', 0)
                label = s.get('label', '?')
                reqs = s.get('request_count', 0)
                duration = s.get('duration', 0)
                h_reason = s.get('heuristic_reason', '')[:35]
                
                # Color score
                if score > 0.7:
                    score_str = _colorize(f"{score:.2f}", 'red')
                elif score > 0.3:
                    score_str = _colorize(f"{score:.2f}", 'yellow')
                else:
                    score_str = _colorize(f"{score:.2f}", 'green')
                
                # Color label
                label_str = _colorize(label, 'red' if label == 'bot' else 'green')
                
                # Risk label
                risk = score_label(score)
                risk_color = score_label_color(score)
                risk_str = _colorize(f"{risk:<9}", risk_color)
                
                # Score bar
                bar = _score_bar(score, 10)
                
                # Duration color (fast = suspicious, normal = fine)
                if duration < 1.0 and reqs > 5:
                    dur_str = _colorize(f"{duration:.1f}s", 'red')
                elif duration > 300:
                    dur_str = _colorize(f"{duration:.0f}s", 'cyan')
                else:
                    dur_str = f"{duration:.1f}s"
                
                lines.append(
                    f"  {ip:<18} {score_str:>5} {bar} {risk_str} {label_str:<7} {reqs:>5} "
                    f"{dur_str:>6} {h_reason}"
                )
            
            lines.append("")
    
    # Recommendations
    lines.append(_colorize("  Recommendations:", "bold"))
    lines.append("  ─────────────────")
    
    if bot_rate > 0.3:
        lines.append(_colorize("  🚨 High bot traffic detected. Consider:", 'red'))
        lines.append("     • Implement rate limiting on suspicious IPs")
        lines.append("     • Add CAPTCHA for repeated login attempts")
        lines.append("     • Review and block known bot user agents")
    elif bot_rate > 0.1:
        lines.append(_colorize("  ⚠️  Moderate bot traffic detected. Consider:", 'yellow'))
        lines.append("     • Monitor top suspicious sessions")
        lines.append("     • Consider rate limiting for high-frequency endpoints")
    else:
        lines.append(_colorize("  ✅ Low bot traffic. Your API looks healthy.", 'green'))
        lines.append("     • Continue monitoring periodically")
    
    lines.append("")
    
    return "\n".join(lines)


def format_json(results: dict[str, Any]) -> str:
    """Format results as JSON.
    
    Args:
        results: Same dictionary as format_terminal
    """
    return json.dumps(results, indent=2, default=str)


def format_json_pretty(results: dict[str, Any]) -> str:
    """Format results as human-friendly colored JSON for terminal reading.
    
    Adds ANSI colors to keys, scores, and labels so the JSON
    is scannable without a separate viewer.
    """
    lines = []
    
    def _k(key: str) -> str:
        return _colorize(f'"{key}"', 'cyan')
    
    def _s(val: str) -> str:
        return _colorize(f'"{val}"', 'green')
    
    def _n(val) -> str:
        return _colorize(str(val), 'yellow')
    
    def _score_color(val: float) -> str:
        if val > 0.7:
            return _colorize(f'{val}', 'red')
        elif val > 0.3:
            return _colorize(f'{val}', 'yellow')
        else:
            return _colorize(f'{val}', 'green')
    
    def _label_color(label: str) -> str:
        return _colorize(f'"{label}"', 'red' if label == 'bot' else 'green')
    
    # Detect scan vs probe format
    is_probe = 'combined_score' in results and 'sessions' not in results
    
    if is_probe:
        return _format_json_pretty_probe(results, _k, _s, _n, _score_color, _label_color)
    
    # --- Scan format ---
    total = results.get('total_sessions', 0)
    bots = results.get('bot_count', 0)
    humans = results.get('human_count', 0)
    bot_rate = results.get('bot_rate', 0.0)
    
    lines.append('{')
    lines.append(f'  {_k("summary")}: {{')
    lines.append(f'    {_k("total_sessions")}: {_n(total)},')
    lines.append(f'    {_k("bot_sessions")}: {_colorize(str(bots), "red")},')
    lines.append(f'    {_k("human_sessions")}: {_colorize(str(humans), "green")},')
    lines.append(f'    {_k("bot_rate")}: {_score_color(bot_rate)}')
    lines.append('  },')
    lines.append(f'  {_k("threshold")}: {_n(results.get("threshold", 0.7))},')
    lines.append(f'  {_k("model_used")}: {_colorize(str(results.get("model_used", False)), "yellow")},')
    lines.append('')
    lines.append(f'  {_k("sessions")}: [')
    
    sessions = results.get('sessions', [])
    sorted_sessions = sorted(sessions, key=lambda s: s.get('score', 0), reverse=True)
    
    for i, s in enumerate(sorted_sessions):
        score = s.get('score', 0)
        label = s.get('label', '?')
        risk = score_label(score)
        
        comma = ',' if i < len(sorted_sessions) - 1 else ''
        lines.append('  {')
        lines.append(f'    {_k("ip")}: {_s(s.get("ip", "?"))},')
        lines.append(f'    {_k("score")}: {_score_color(score)},')
        lines.append(f'    {_k("risk")}: {_colorize(f"\"{risk}\"", score_label_color(score))},')
        lines.append(f'    {_k("label")}: {_label_color(label)},')
        lines.append(f'    {_k("heuristic")}: {{')
        lines.append(f'      {_k("label")}: {_label_color(s.get("heuristic_label", "?"))},')
        lines.append(f'      {_k("confidence")}: {_n(s.get("heuristic_confidence", 0))},')
        lines.append(f'      {_k("reason")}: {_s(s.get("heuristic_reason", ""))}')
        lines.append('    },')
        lines.append(f'    {_k("model_score")}: {_n(s.get("model_score", 0))},')
        lines.append(f'    {_k("requests")}: {_n(s.get("request_count", 0))},')
        dur = f"{s.get('duration', 0):.1f}"
        lines.append(f'    {_k("duration")}: {_n(dur)},')
        lines.append(f'    {_k("top_endpoint")}: {_s(s.get("top_endpoint", "?"))},')
        lines.append(f'    {_k("user_agent")}: {_s(s.get("user_agent", "?")[:80])}')
        lines.append(f'  }}{comma}')
    
    lines.append('  ]')
    lines.append('}')
    
    return '\n'.join(lines)


def _format_json_pretty_probe(results: dict, _k, _s, _n, _score_color, _label_color) -> str:
    """Format probe results as colored JSON."""
    lines = []
    score = results.get('combined_score', 0)
    label = results.get('label', '?')
    risk = score_label(score)
    
    lines.append('{')
    lines.append(f'  {_k("url")}: {_s(results.get("url", "?"))},')
    status_code = results.get('status_code', 0)
    lines.append(f'  {_k("status")}: {_n(f"HTTP {status_code}")},')
    lines.append(f'  {_k("probes")}: {_n(results.get("probes", 0))},')
    lines.append(f'  {_k("score")}: {_score_color(score)},')
    risk_json = f'"{risk}"'
    lines.append(f'  {_k("risk")}: {_colorize(risk_json, score_label_color(score))},')
    lines.append(f'  {_k("label")}: {_label_color(label)},')
    lines.append(f'  {_k("threshold")}: {_n(results.get("threshold", 0.7))},')
    lines.append('')
    lines.append(f'  {_k("analysis")}: {{')
    lines.append(f'    {_k("heuristic")}: {{')
    lines.append(f'      {_k("score")}: {_n(results.get("heuristic_score", 0))},')
    lines.append(f'      {_k("reason")}: {_s(results.get("heuristic_reason", ""))}')
    lines.append('    },')
    ms = results.get('model_score', 0)
    lines.append(f'    {_k("model")}: {_n(ms)}')
    lines.append('  },')
    lines.append('')
    
    # Features
    features = results.get('features', {})
    if features:
        lines.append(f'  {_k("features")}: {{')
        items = list(features.items())
        for i, (fname, fval) in enumerate(items):
            comma = ',' if i < len(items) - 1 else ''
            lines.append(f'    {_k(fname)}: {_n(f"{fval:.4f}")}{comma}')
        lines.append('  },')
    
    # Timing
    timing = results.get('timing', {})
    if timing:
        lines.append(f'  {_k("timing")}: {{')
        items = list(timing.items())
        for i, (tname, tval) in enumerate(items):
            comma = ',' if i < len(items) - 1 else ''
            lines.append(f'    {_k(tname)}: {_n(f"{tval:.4f}")}{comma}')
        lines.append('  }')
    
    lines.append('}')
    return '\n'.join(lines)


def format_html(results: dict[str, Any]) -> str:
    """Format results as a shareable HTML report.
    
    Args:
        results: Same dictionary as format_terminal
    
    Returns:
        Complete HTML document string
    """
    total = results.get('total_sessions', 0)
    bots = results.get('bot_count', 0)
    humans = results.get('human_count', 0)
    bot_rate = results.get('bot_rate', 0.0)
    sessions = results.get('sessions', [])
    threshold = results.get('threshold', 0.7)
    model_used = results.get('model_used', False)
    summary = results.get('summary', {})
    error_message = results.get('error')

    # Sort sessions by score descending
    sorted_sessions = sorted(sessions, key=lambda s: s.get('score', 0), reverse=True)

    # Determine status level
    if error_message:
        status_text = 'No Data'
        status_color = '#6b7280'
    elif bot_rate < 0.1:
        status_text = 'Healthy'
        status_color = '#10b981'
    elif bot_rate < 0.3:
        status_text = 'Warning'
        status_color = '#f59e0b'
    else:
        status_text = 'Critical'
        status_color = '#ef4444'

    # Donut "bot" slice is neutral gray (not red) when there's no session
    # data — an empty ring shouldn't read as 100% bot traffic.
    donut_bot_color = '#ef4444' if total > 0 else '#6b7280'

    error_banner = ''
    if error_message:
        error_banner = f"""
        <div class="rec-card warning">
            <h3>⚠️ {error_message}</h3>
            <p>Check that the log file matches the expected format (nginx combined or JSON) and isn't empty.</p>
        </div>"""
    
    # Build session table rows
    session_rows = ''
    for i, s in enumerate(sorted_sessions):
        score = s.get('score', 0)
        label = s.get('label', '?')
        
        if score > 0.7:
            score_class = 'score-high'
            risk = score_label(score)
        elif score > 0.3:
            score_class = 'score-medium'
            risk = score_label(score)
        else:
            score_class = 'score-low'
            risk = score_label(score)
        
        label_class = 'label-bot' if label == 'bot' else 'label-human'
        
        # Truncate long values
        ip = s.get('ip', '?')
        ua = s.get('user_agent', '?')
        if len(ua) > 40:
            ua = ua[:37] + '...'
        top_ep = s.get('top_endpoint', '?')
        if len(top_ep) > 30:
            top_ep = top_ep[:27] + '...'
        
        heuristic_reason = s.get('heuristic_reason', '')
        if len(heuristic_reason) > 50:
            heuristic_reason = heuristic_reason[:47] + '...'
        
        session_rows += f"""
        <tr>
            <td class="mono">{ip}</td>
            <td><span class="score-badge {score_class}">{score:.2f}</span></td>
            <td><span class="risk-badge risk-{risk.lower()}">{risk}</span></td>
            <td><span class="label-badge {label_class}">{label}</span></td>
            <td>{s.get('request_count', 0)}</td>
            <td>{s.get('duration', 0):.1f}s</td>
            <td class="mono">{top_ep}</td>
            <td title="{s.get('user_agent', '')}">{ua}</td>
            <td title="{s.get('heuristic_reason', '')}">{heuristic_reason}</td>
        </tr>"""
    
    # Recommendations
    recommendations = ''
    if error_message:
        recommendations = f"""
        <div class="rec-card warning">
            <h3>⚠️ No Valid Log Entries</h3>
            <ul>
                <li>{error_message}</li>
                <li>Check that the log file matches the expected format (nginx combined or JSON)</li>
                <li>Verify the file isn't empty</li>
            </ul>
        </div>"""
    elif bot_rate > 0.3:
        recommendations = """
        <div class="rec-card danger">
            <h3>🚨 High Bot Traffic Detected</h3>
            <ul>
                <li>Implement rate limiting on top suspicious IPs</li>
                <li>Add CAPTCHA for login and signup endpoints</li>
                <li>Block known bot user agents at the WAF level</li>
                <li>Review and harden API endpoints being targeted</li>
            </ul>
        </div>"""
    elif bot_rate > 0.1:
        recommendations = """
        <div class="rec-card warning">
            <h3>⚠️ Moderate Bot Traffic</h3>
            <ul>
                <li>Monitor top suspicious sessions over time</li>
                <li>Consider rate limiting high-frequency endpoints</li>
                <li>Review flagged sessions for false positives</li>
            </ul>
        </div>"""
    else:
        recommendations = """
        <div class="rec-card good">
            <h3>✅ Low Bot Traffic</h3>
            <ul>
                <li>Your API looks healthy</li>
                <li>Continue periodic monitoring</li>
                <li>Consider setting up automated scans</li>
            </ul>
        </div>"""
    
    # Donut chart data (CSS-only)
    human_pct = humans / total * 100 if total > 0 else 0
    bot_pct = bots / total * 100 if total > 0 else 0
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Microguard Report — Bot Traffic Analysis</title>
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
            --purple: #8b5cf6;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2rem;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            text-align: center;
            margin-bottom: 2rem;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--card-border);
        }}
        .header h1 {{
            font-size: 2rem;
            margin-bottom: 0.5rem;
        }}
        .header h1 span {{ color: var(--blue); }}
        .header .subtitle {{ color: var(--text-muted); font-size: 0.9rem; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .stat-card {{
            background: var(--card);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 1.5rem;
            text-align: center;
        }}
        .stat-card .value {{
            font-size: 2.5rem;
            font-weight: 700;
            line-height: 1;
            margin-bottom: 0.25rem;
        }}
        .stat-card .label {{
            color: var(--text-muted);
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .stat-card.status {{
            border-color: {status_color};
        }}
        .stat-card.status .value {{ color: {status_color}; }}
        .chart-section {{
            display: flex;
            justify-content: center;
            margin-bottom: 2rem;
        }}
        .donut-chart {{
            width: 200px;
            height: 200px;
            border-radius: 50%;
            position: relative;
            background: conic-gradient(
                var(--green) 0deg {human_pct:.1f}deg,
                {donut_bot_color} {human_pct:.1f}deg 360deg
            );
        }}
        .donut-chart::after {{
            content: '';
            position: absolute;
            top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            width: 120px;
            height: 120px;
            background: var(--bg);
            border-radius: 50%;
        }}
        .donut-center {{
            position: absolute;
            top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
            z-index: 1;
        }}
        .donut-center .big {{ font-size: 1.8rem; font-weight: 700; }}
        .donut-center .small {{ color: var(--text-muted); font-size: 0.75rem; }}
        .legend {{
            display: flex;
            justify-content: center;
            gap: 2rem;
            margin-top: 1rem;
        }}
        .legend-item {{ display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; }}
        .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; }}
        .section-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid var(--card-border);
        }}
        .table-wrapper {{
            background: var(--card);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            overflow-x: auto;
            margin-bottom: 2rem;
        }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
        th {{
            background: #0f172a;
            padding: 0.75rem 1rem;
            text-align: left;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
            white-space: nowrap;
            position: sticky;
            top: 0;
        }}
        td {{
            padding: 0.75rem 1rem;
            border-top: 1px solid var(--card-border);
            white-space: nowrap;
        }}
        tr:hover {{ background: rgba(59, 130, 246, 0.05); }}
        .mono {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.85rem; }}
        .score-badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.85rem;
        }}
        .score-high {{ background: rgba(239, 68, 68, 0.2); color: var(--red); }}
        .score-medium {{ background: rgba(245, 158, 11, 0.2); color: var(--yellow); }}
        .score-low {{ background: rgba(16, 185, 129, 0.2); color: var(--green); }}
        .label-badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
        }}
        .label-bot {{ background: rgba(239, 68, 68, 0.15); color: var(--red); }}
        .label-human {{ background: rgba(16, 185, 129, 0.15); color: var(--green); }}
        .risk-badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .risk-safe {{ background: rgba(16, 185, 129, 0.15); color: var(--green); }}
        .risk-low {{ background: rgba(59, 130, 246, 0.15); color: var(--blue); }}
        .risk-warning {{ background: rgba(245, 158, 11, 0.15); color: var(--yellow); }}
        .risk-danger {{ background: rgba(239, 68, 68, 0.15); color: var(--red); }}
        .rec-card {{
            background: var(--card);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 2rem;
            border-left: 4px solid;
        }}
        .rec-card.danger {{ border-left-color: var(--red); }}
        .rec-card.warning {{ border-left-color: var(--yellow); }}
        .rec-card.good {{ border-left-color: var(--green); }}
        .rec-card h3 {{ margin-bottom: 0.75rem; font-size: 1.1rem; }}
        .rec-card ul {{ padding-left: 1.5rem; color: var(--text-muted); }}
        .rec-card li {{ margin-bottom: 0.5rem; }}
        .footer {{
            text-align: center;
            color: var(--text-muted);
            font-size: 0.8rem;
            margin-top: 2rem;
            padding-top: 1rem;
            border-top: 1px solid var(--card-border);
        }}
        .footer a {{ color: var(--blue); text-decoration: none; }}
        .meta {{
            display: flex;
            justify-content: center;
            gap: 2rem;
            color: var(--text-muted);
            font-size: 0.8rem;
            margin-bottom: 1.5rem;
        }}
        .meta span {{ display: flex; align-items: center; gap: 0.3rem; }}
        @media print {{
            body {{ background: white; color: #1e293b; padding: 1rem; }}
            .stat-card {{ background: #f8fafc; border-color: #e2e8f0; }}
            .stat-card .label {{ color: #64748b; }}
            .table-wrapper {{ background: white; border-color: #e2e8f0; }}
            th {{ background: #f1f5f9; color: #475569; }}
            td {{ border-color: #e2e8f0; }}
            tr:hover {{ background: transparent; }}
            .donut-chart {{ background: conic-gradient(#10b981 0deg {human_pct:.1f}deg, {donut_bot_color} {human_pct:.1f}deg 360deg); }}
            .donut-chart::after {{ background: white; }}
            .header h1 span {{ color: #2563eb; }}
            .rec-card {{ background: #f8fafc; }}
            .footer {{ border-color: #e2e8f0; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 <span>Microguard</span> Bot Traffic Report</h1>
            <div class="subtitle">Generated by micrograd-powered bot detection</div>
        </div>
        
        <div class="meta">
            <span>📊 {summary.get('total_entries', 0):,} log entries analyzed</span>
            <span>👥 {total} sessions found</span>
            <span>🧠 Model: {'Enabled' if model_used else 'Heuristic only'}</span>
            <span>🎯 Threshold: {threshold:.0%}</span>
        </div>
        {error_banner}
        <div class="stats-grid">
            <div class="stat-card status">
                <div class="value">{bot_rate:.1%}</div>
                <div class="label">Bot Traffic</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: var(--red)">{bots}</div>
                <div class="label">Bot Sessions</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: var(--green)">{humans}</div>
                <div class="label">Human Sessions</div>
            </div>
            <div class="stat-card">
                <div class="value" style="color: var(--blue)">{total}</div>
                <div class="label">Total Sessions</div>
            </div>
        </div>
        
        <div class="chart-section">
            <div>
                <div class="donut-chart">
                    <div class="donut-center">
                        <div class="big" style="color: {status_color}">{status_text}</div>
                        <div class="small">Status</div>
                    </div>
                </div>
                <div class="legend">
                    <div class="legend-item">
                        <div class="legend-dot" style="background: var(--green)"></div>
                        Human ({humans}, {human_pct:.1f}%)
                    </div>
                    <div class="legend-item">
                        <div class="legend-dot" style="background: var(--red)"></div>
                        Bot ({bots}, {bot_pct:.1f}%)
                    </div>
                </div>
            </div>
        </div>
        
        {recommendations}
        
        <div class="section-title">Top Suspicious Sessions</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>IP Address</th>
                        <th>Score</th>
                        <th>Risk</th>
                        <th>Label</th>
                        <th>Requests</th>
                        <th>Duration</th>
                        <th>Top Endpoint</th>
                        <th>User Agent</th>
                        <th>Reason</th>
                    </tr>
                </thead>
                <tbody>
                    {session_rows}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>Powered by <a href="https://github.com/karpathy/micrograd">micrograd</a> · Microguard v0.1.0</p>
            <p>Score scale: <strong style="color: var(--green)">SAFE</strong> (0.00-0.30) · <strong style="color: var(--blue)">LOW</strong> (0.31-0.59) · <strong style="color: var(--yellow)">WARNING</strong> (0.60-0.79) · <strong style="color: var(--red)">DANGER</strong> (0.80-1.00)</p>
            <p>Threshold: {threshold:.0%} — sessions above this score are classified as bots</p>
        </div>
    </div>
</body>
</html>"""
    
    return html


def _danger_ips(results: dict[str, Any]) -> list[str]:
    """Return sorted, deduped IPs from sessions scored in the DANGER bucket."""
    ips = {
        s['ip']
        for s in results.get('sessions', [])
        if score_label(s.get('score', 0)) == 'DANGER' and s.get('ip')
    }
    return sorted(ips)


def format_nginx_denylist(results: dict[str, Any]) -> str:
    """Format DANGER-scored session IPs as an nginx deny-list include file."""
    if results.get('error'):
        return f"# microguard: {results['error']} — nothing to block\n"

    ips = _danger_ips(results)
    if not ips:
        return "# microguard: no sessions scored DANGER — nothing to block\n"

    lines = [
        f"# microguard nginx deny-list — {len(ips)} IP(s) scored DANGER",
        "# Generated by `microguard scan --output nginx` — include this file in a server block.",
    ]
    lines.extend(f"deny {ip};" for ip in ips)
    return '\n'.join(lines) + '\n'


def format_cloudflare_rule(results: dict[str, Any]) -> str:
    """Format DANGER-scored session IPs as a Cloudflare Firewall Rule expression."""
    if results.get('error'):
        return f"# microguard: {results['error']} — nothing to block\n"

    ips = _danger_ips(results)
    if not ips:
        return "# microguard: no sessions scored DANGER — nothing to block\n"

    expression = f"(ip.src in {{{' '.join(ips)}}})"
    lines = [
        f"# microguard Cloudflare Firewall Rule — {len(ips)} IP(s) scored DANGER",
        "# Paste this expression into Cloudflare's rule builder (or the Rulesets API) with action: Block.",
        expression,
    ]
    return '\n'.join(lines) + '\n'


def format_verbose(results: dict[str, Any]) -> str:
    """Format results with full feature vectors and heuristic rule details.
    
    Args:
        results: Same dictionary as format_terminal
    """
    lines = []
    
    lines.append("")
    lines.append(_colorize("  Microguard Bot Traffic Report — Verbose", "bold"))
    lines.append(_colorize("  ──────────────────────────────────────", "cyan"))
    lines.append("")
    
    total = results.get('total_sessions', 0)
    bots = results.get('bot_count', 0)
    humans = results.get('human_count', 0)
    bot_rate = results.get('bot_rate', 0.0)
    
    if bot_rate < 0.1:
        status_icon = '✅'
    elif bot_rate < 0.3:
        status_icon = '⚠️ '
    else:
        status_icon = '🚨'
    
    lines.append(f"  {status_icon} Bot Traffic: {bot_rate:.1%}")
    lines.append(f"  Total: {total} sessions ({humans} human, {bots} bot)")
    lines.append("")
    
    sessions = results.get('sessions', [])
    sorted_sessions = sorted(sessions, key=lambda s: s.get('score', 0), reverse=True)
    
    for i, s in enumerate(sorted_sessions):
        score = s.get('score', 0)
        label = s.get('label', '?')
        risk = score_label(score)
        risk_color = score_label_color(score)
        
        lines.append(_colorize(f"  ━━ Session {i+1}: {s['ip']} ━━", "bold"))
        lines.append(f"  Score: {_colorize(f'{score:.3f}', risk_color)} ({risk}) → {label}")
        lines.append(f"  User-Agent: {s.get('user_agent', '?')}")
        lines.append(f"  Requests: {s.get('request_count', 0)}  Duration: {s.get('duration', 0):.1f}s  Top: {s.get('top_endpoint', '?')}")
        lines.append("")
        
        # Heuristic analysis
        h_label = s.get('heuristic_label', '?')
        h_conf = s.get('heuristic_confidence', 0)
        h_reason = s.get('heuristic_reason', '')
        m_score = s.get('model_score', 0)
        
        lines.append(f"  Heuristic: {_colorize(f'{h_label} ({h_conf:.2f})', 'red' if h_label == 'bot' else 'green')}")
        lines.append(f"    Rule: {h_reason}")
        lines.append(f"  ML Model:  {m_score:.3f}")
        lines.append("")
        
        # Feature vector
        features = s.get('features', {})
        if features:
            lines.append(_colorize("  Feature Vector (19 dimensions):", "cyan"))
            lines.append("  " + "─" * 55)
            lines.append(f"  {'Feature':<35} {'Value':>10}  {'Status'}")
            lines.append("  " + "─" * 55)
            
            # Classify features as bot/human indicators
            bot_indicators = [
                'time_since_last_request', 'requests_per_minute_1m',
                'requests_per_minute_5m', 'same_endpoint_hits',
                'method_mismatch_count', 'error_rate',
            ]
            human_indicators = [
                'inter_request_time_cv', 'endpoint_count',
                'endpoint_sequence_entropy', 'unique_endpoint_ratio',
                'has_accept_language', 'header_consistency_score',
            ]
            
            for name, val in features.items():
                if name in bot_indicators and val > 0:
                    indicator = _colorize('▲ bot', 'red')
                elif name in human_indicators and val > 0:
                    indicator = _colorize('▼ human', 'green')
                elif name == 'ua_category':
                    cats = {0.0: 'browser', 1.0: 'bot', 2.0: 'unknown'}
                    indicator = cats.get(val, '?')
                    indicator = _colorize(f'  {indicator}', 'red' if val == 1.0 else 'green' if val == 0.0 else 'yellow')
                elif name == 'field_fill_speed' and val == 0.0:
                    indicator = 'N/A (logs)'
                else:
                    indicator = ''
                
                lines.append(f"  {name:<35} {val:>10.4f}  {indicator}")
            
            lines.append("  " + "─" * 55)
        
        lines.append("")
    
    return "\n".join(lines)


def print_report(results: dict[str, Any], fmt: str = 'terminal'):
    """Print report to stdout.
    
    Args:
        results: Scan results dictionary
        fmt: 'terminal', 'json', or 'html'
    """
    if fmt == 'json':
        print(format_json(results))
    elif fmt == 'html':
        print(format_html(results))
    elif fmt == 'nginx':
        print(format_nginx_denylist(results))
    elif fmt == 'cloudflare':
        print(format_cloudflare_rule(results))
    else:
        print(format_terminal(results))
