"""Report formatter for bot detection results.

Supports terminal table and JSON output formats.
"""

import json
import sys
from typing import List, Dict, Any, Optional


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


def format_terminal(results: Dict[str, Any]) -> str:
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
    
    # Summary stats
    total = results.get('total_sessions', 0)
    bots = results.get('bot_count', 0)
    humans = results.get('human_count', 0)
    bot_rate = results.get('bot_rate', 0.0)
    
    # Status color based on bot rate
    if bot_rate < 0.1:
        status_color = 'green'
        status_icon = '✅'
    elif bot_rate < 0.3:
        status_color = 'yellow'
        status_icon = '⚠️ '
    else:
        status_color = 'red'
        status_icon = '🚨'
    
    lines.append(f"  {status_icon} {_colorize(f'Bot Traffic: {bot_rate:.1%}', status_color)}")
    lines.append("")
    lines.append(f"  Total sessions:  {total}")
    lines.append(f"  Human sessions:  {_colorize(str(humans), 'green')}")
    lines.append(f"  Bot sessions:    {_colorize(str(bots), 'red')}")
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
                f"  {'IP':<20} {'Score':>6} {'Label':<8} {'Reqs':>5} "
                f"{'Duration':>8} {'Top Endpoint'}"
            )
            lines.append("  " + "─" * 70)
            
            for s in top_n:
                ip = s.get('ip', '?')[:20]
                score = s.get('score', 0)
                label = s.get('label', '?')
                reqs = s.get('request_count', 0)
                duration = s.get('duration', 0)
                top_url = s.get('top_endpoint', '?')[:30]
                
                # Color score
                if score > 0.7:
                    score_str = _colorize(f"{score:.2f}", 'red')
                elif score > 0.3:
                    score_str = _colorize(f"{score:.2f}", 'yellow')
                else:
                    score_str = _colorize(f"{score:.2f}", 'green')
                
                # Color label
                label_str = _colorize(label, 'red' if label == 'bot' else 'green')
                
                lines.append(
                    f"  {ip:<20} {score_str:>6} {label_str:<8} {reqs:>5} "
                    f"{duration:>7.1f}s {top_url}"
                )
            
            lines.append("")
    
    # Recommendations
    lines.append(_colorize("  Recommendations:", "bold"))
    lines.append("  ─────────────────")
    
    if bot_rate > 0.3:
        lines.append("  🚨 High bot traffic detected. Consider:")
        lines.append("     • Implement rate limiting on suspicious IPs")
        lines.append("     • Add CAPTCHA for repeated login attempts")
        lines.append("     • Review and block known bot user agents")
    elif bot_rate > 0.1:
        lines.append("  ⚠️  Moderate bot traffic detected. Consider:")
        lines.append("     • Monitor top suspicious sessions")
        lines.append("     • Consider rate limiting for high-frequency endpoints")
    else:
        lines.append("  ✅ Low bot traffic. Your API looks healthy.")
        lines.append("     • Continue monitoring periodically")
    
    lines.append("")
    
    return "\n".join(lines)


def format_json(results: Dict[str, Any]) -> str:
    """Format results as JSON.
    
    Args:
        results: Same dictionary as format_terminal
    """
    return json.dumps(results, indent=2, default=str)


def format_html(results: Dict[str, Any]) -> str:
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
    
    # Sort sessions by score descending
    sorted_sessions = sorted(sessions, key=lambda s: s.get('score', 0), reverse=True)
    
    # Determine status level
    if bot_rate < 0.1:
        status_level = 'good'
        status_text = 'Healthy'
        status_color = '#10b981'
    elif bot_rate < 0.3:
        status_level = 'warning'
        status_text = 'Warning'
        status_color = '#f59e0b'
    else:
        status_level = 'danger'
        status_text = 'Critical'
        status_color = '#ef4444'
    
    # Build session table rows
    session_rows = ''
    for i, s in enumerate(sorted_sessions):
        score = s.get('score', 0)
        label = s.get('label', '?')
        
        if score > 0.7:
            score_class = 'score-high'
        elif score > 0.3:
            score_class = 'score-medium'
        else:
            score_class = 'score-low'
        
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
            <td><span class="label-badge {label_class}">{label}</span></td>
            <td>{s.get('request_count', 0)}</td>
            <td>{s.get('duration', 0):.1f}s</td>
            <td class="mono">{top_ep}</td>
            <td title="{s.get('user_agent', '')}">{ua}</td>
            <td title="{s.get('heuristic_reason', '')}">{heuristic_reason}</td>
        </tr>"""
    
    # Recommendations
    recommendations = ''
    if bot_rate > 0.3:
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
                var(--red) {human_pct:.1f}deg 360deg
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
            .donut-chart {{ background: conic-gradient(#10b981 0deg {human_pct:.1f}deg, #ef4444 {human_pct:.1f}deg 360deg); }}
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
            <p>Report generated for bot traffic analysis. Threshold: {threshold:.0%} (above = bot)</p>
        </div>
    </div>
</body>
</html>"""
    
    return html


def print_report(results: Dict[str, Any], fmt: str = 'terminal'):
    """Print report to stdout.
    
    Args:
        results: Scan results dictionary
        fmt: 'terminal', 'json', or 'html'
    """
    if fmt == 'json':
        print(format_json(results))
    elif fmt == 'html':
        print(format_html(results))
    else:
        print(format_terminal(results))
