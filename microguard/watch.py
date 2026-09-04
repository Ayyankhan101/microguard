"""Continuous log file monitoring for real-time bot detection.

Tails a log file and analyzes new entries as they appear,
printing bot detections to the terminal in real time.
"""

import os
import sys
import time
from datetime import datetime
from typing import Optional

from .parser import parse_nginx_line, parse_json_line, detect_format, LogEntry
from .features import group_into_sessions, extract_features, FEATURE_NAMES, Session
from .labeler import label_session
from .model import BotDetector
from .report import score_label, score_label_color, _colorize


def _read_new_lines(filepath: str, offset: int, fmt: str):
    """Read new lines from a file starting at byte offset.
    
    Returns:
        (new_lines, new_offset)
    """
    try:
        with open(filepath, 'r', errors='replace') as f:
            f.seek(offset)
            new_data = f.read()
            new_offset = f.tell()
    except FileNotFoundError:
        return [], offset
    
    if not new_data:
        return [], offset
    
    entries = []
    for line in new_data.splitlines():
        line = line.strip()
        if not line:
            continue
        if fmt == 'json':
            entry = parse_json_line(line)
        else:
            entry = parse_nginx_line(line)
        if entry:
            entries.append(entry)
    
    return entries, new_offset


def _format_detection(entry: LogEntry, label: str, confidence: float, 
                       reason: str, model_score: float, combined_score: float) -> str:
    """Format a single bot detection for terminal output."""
    risk = score_label(combined_score)
    risk_color = score_label_color(combined_score)
    
    ts = entry.timestamp.strftime('%H:%M:%S')
    ip = entry.ip[:18]
    url = entry.url[:35]
    ua = entry.user_agent[:30]
    
    lines = []
    lines.append("")
    lines.append(_colorize(f"  🚨 Bot Detection — {ts}", "bold"))
    lines.append(f"  {'─' * 55}")
    lines.append(f"  IP:        {_colorize(ip, 'cyan')}")
    lines.append(f"  Score:     {_colorize(f'{combined_score:.3f}', risk_color)} ({risk})")
    lines.append(f"  Label:     {_colorize(label, 'red' if label == 'bot' else 'green')}")
    lines.append(f"  Request:   {entry.method} {url} → HTTP {entry.status}")
    lines.append(f"  User-Agent: {ua}")
    lines.append(f"  Heuristic: {confidence:.2f} — {reason}")
    if model_score > 0:
        lines.append(f"  ML Model:  {model_score:.3f}")
    lines.append(f"  {'─' * 55}")
    
    return '\n'.join(lines)


def watch_logfile(
    filepath: str,
    fmt: str = 'auto',
    threshold: float = 0.7,
    model_path: Optional[str] = None,
    interval: float = 2.0,
    session_timeout: int = 5,
):
    """Continuously monitor a log file for bot traffic.
    
    Args:
        filepath: Path to the log file to monitor
        fmt: Log format ('auto', 'nginx', 'json')
        threshold: Bot score threshold
        model_path: Path to pre-trained model
        interval: Polling interval in seconds
        session_timeout: Session grouping timeout in minutes
    """
    # Validate file exists
    if not os.path.exists(filepath):
        print(f"❌ Error: Log file not found: {filepath}", file=sys.stderr)
        print(f"   Waiting for file to appear...", file=sys.stderr)
    
    # Auto-detect format
    if fmt == 'auto' and os.path.exists(filepath):
        fmt = detect_format(filepath)
        if fmt == 'unknown':
            fmt = 'nginx'  # Default
    
    # Load model
    model = None
    if model_path and os.path.exists(model_path):
        try:
            model = BotDetector(model_path)
        except Exception:
            pass
    
    # Stats
    total_scanned = 0
    total_bots = 0
    total_humans = 0
    last_report = time.time()
    
    print(_colorize("  Microguard Watch Mode", "bold"))
    print(f"  Monitoring: {filepath}")
    print(f"  Format:     {fmt}")
    print(f"  Threshold:  {threshold}")
    print(f"  Model:      {'loaded' if model else 'heuristic only'}")
    print(f"  Polling:    every {interval}s")
    print(f"  Press Ctrl+C to stop")
    print(f"  {'─' * 55}")
    print()
    
    offset = 0
    if os.path.exists(filepath):
        offset = os.path.getsize(filepath)
    
    try:
        while True:
            time.sleep(interval)
            
            if not os.path.exists(filepath):
                continue
            
            # Read new lines
            entries, new_offset = _read_new_lines(filepath, offset, fmt)
            
            if not entries or new_offset == offset:
                # Periodic status update
                if time.time() - last_report > 30:
                    now = datetime.now().strftime('%H:%M:%S')
                    print(f"  [{now}] Watching... ({total_scanned} scanned, {total_bots} bots, {total_humans} human)")
                    last_report = time.time()
                continue
            
            offset = new_offset
            total_scanned += len(entries)
            
            # Group into mini-sessions for analysis
            sessions = group_into_sessions(entries, session_timeout)
            
            for session in sessions:
                if session.request_count == 0:
                    continue
                
                features = extract_features(session)
                h_label, h_conf, h_reason = label_session(session)
                
                # Get model prediction
                if model:
                    m_score = model.predict(features)
                    combined = 0.6 * m_score + 0.4 * h_conf
                    if h_label == 'bot':
                        combined = max(combined, h_conf)
                else:
                    m_score = 0.0
                    combined = h_conf if h_label == 'bot' else (1.0 - h_conf)
                
                is_bot = combined >= threshold
                label = 'bot' if is_bot else 'human'
                
                if is_bot:
                    total_bots += 1
                    # Show detection for bot entries
                    for entry in session.requests[:3]:  # Show up to 3 entries
                        print(_format_detection(
                            entry, label, h_conf, h_reason, m_score, combined
                        ))
                else:
                    total_humans += 1
            
            # Periodic summary
            if time.time() - last_report > 30:
                now = datetime.now().strftime('%H:%M:%S')
                bot_rate = total_bots / max(total_bots + total_humans, 1)
                print(f"  [{now}] Stats: {total_scanned} scanned, "
                      f"{_colorize(str(total_bots), 'red')} bots, "
                      f"{_colorize(str(total_humans), 'green')} human "
                      f"({bot_rate:.0%} bot rate)")
                last_report = time.time()
    
    except KeyboardInterrupt:
        print()
        print(f"  {'─' * 55}")
        print(f"  Watch stopped.")
        print(f"  Total: {total_scanned} entries scanned")
        print(f"  Bots:  {_colorize(str(total_bots), 'red')}")
        print(f"  Human: {_colorize(str(total_humans), 'green')}")
        bot_rate = total_bots / max(total_bots + total_humans, 1)
        print(f"  Rate:  {bot_rate:.1%}")
        print()
