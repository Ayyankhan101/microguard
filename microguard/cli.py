"""CLI entry point for microguard bot detection tool.

Usage:
    microguard scan <logfile> [--format terminal|json] [--threshold 0.7]
    microguard scan --help
"""

import argparse
import json
import os
import sys
from typing import Any

from .features import FEATURE_NAMES, extract_features, group_into_sessions
from .labeler import label_session
from .model import BotDetector
from .parser import LogEntry, parse_file
from .report import print_report

# Default threshold for bot classification
DEFAULT_THRESHOLD = 0.7

# Path to pre-trained model (relative to package)
DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'model.json'
)


def scan_logfile(
    filepath: str,
    fmt: str = 'auto',
    threshold: float = DEFAULT_THRESHOLD,
    model_path: str = DEFAULT_MODEL_PATH,
    timeout_minutes: int = 30,
) -> dict[str, Any]:
    """Scan a log file for bot traffic.
    
    Args:
        filepath: Path to the log file
        fmt: Log format ('auto', 'nginx', 'json')
        threshold: Bot score threshold (above this = bot)
        model_path: Path to pre-trained model
        timeout_minutes: Session timeout in minutes
    
    Returns:
        Dictionary with scan results
    """
    # Parse log file
    print(f"📂 Parsing {filepath}...", file=sys.stderr)
    
    entries: list[LogEntry] = list(parse_file(filepath, fmt))
    
    if not entries:
        return {
            'total_sessions': 0,
            'bot_count': 0,
            'human_count': 0,
            'bot_rate': 0.0,
            'sessions': [],
            'error': 'No valid log entries found',
        }
    
    print(f"📊 Found {len(entries)} log entries", file=sys.stderr)
    
    # Group into sessions
    sessions = group_into_sessions(entries, timeout_minutes)
    print(f"👥 Grouped into {len(sessions)} sessions", file=sys.stderr)
    
    # Load model (if available)
    model = None
    model_available = os.path.exists(model_path)
    
    if model_available:
        try:
            model = BotDetector(model_path)
            print("🧠 Loaded pre-trained model", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — model load is best-effort, falls back to heuristics
            print(f"⚠️  Could not load model: {e}", file=sys.stderr)
            print("   Falling back to heuristic rules only", file=sys.stderr)
    else:
        print("📋 No pre-trained model found, using heuristic rules", file=sys.stderr)
    
    # Analyze each session
    session_results = []
    bot_count = 0
    human_count = 0
    
    for session in sessions:
        # Extract features
        features = extract_features(session)
        
        # Get label from heuristic rules
        heuristic_label, heuristic_conf, heuristic_reason = label_session(session)
        
        # Get model prediction (if available)
        if model is not None:
            model_score = model.predict(features)
            # Combine heuristic and model scores
            # Weight: 60% model, 40% heuristic
            combined_score = 0.6 * model_score + 0.4 * heuristic_conf
            if heuristic_label == 'bot':
                combined_score = max(combined_score, heuristic_conf)
        else:
            # Use heuristic confidence as score
            model_score = 0.0
            combined_score = heuristic_conf if heuristic_label == 'bot' else (1.0 - heuristic_conf)
        
        # Classify
        is_bot = combined_score >= threshold
        label = 'bot' if is_bot else 'human'
        
        if is_bot:
            bot_count += 1
        else:
            human_count += 1
        
        # Get top endpoint
        from collections import Counter
        urls = [e.url.split('?')[0] for e in session.requests]
        top_endpoint = Counter(urls).most_common(1)[0][0] if urls else '?'
        
        session_results.append({
            'ip': session.ip,
            'score': combined_score,
            'model_score': model_score,
            'heuristic_label': heuristic_label,
            'heuristic_confidence': heuristic_conf,
            'heuristic_reason': heuristic_reason,
            'label': label,
            'request_count': session.request_count,
            'duration': session.duration,
            'top_endpoint': top_endpoint,
            'user_agent': session.user_agent[:100],
            'features': {name: val for name, val in zip(FEATURE_NAMES, features)},
        })
    
    total = len(sessions)
    bot_rate = bot_count / total if total > 0 else 0.0
    
    return {
        'total_sessions': total,
        'bot_count': bot_count,
        'human_count': human_count,
        'bot_rate': bot_rate,
        'threshold': threshold,
        'model_used': model is not None,
        'sessions': session_results,
        'summary': {
            'total_entries': len(entries),
            'total_sessions': total,
            'bot_sessions': bot_count,
            'human_sessions': human_count,
            'bot_rate': bot_rate,
        },
    }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog='microguard',
        description='🔍 Microguard — Bot Traffic Audit Tool',
        epilog='Powered by micrograd. Detect malicious bot traffic in your API logs.',
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # scan command
    scan_parser = subparsers.add_parser(
        'scan',
        help='Scan a log file for bot traffic'
    )
    scan_parser.add_argument(
        'logfile',
        help='Path to the log file to analyze'
    )
    scan_parser.add_argument(
        '--format', '-f',
        choices=['auto', 'nginx', 'json'],
        default='auto',
        help='Log file format (default: auto-detect)'
    )
    scan_parser.add_argument(
        '--threshold', '-t',
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f'Bot score threshold (default: {DEFAULT_THRESHOLD})'
    )
    scan_parser.add_argument(
        '--model', '-m',
        default=DEFAULT_MODEL_PATH,
        help='Path to pre-trained model file'
    )
    scan_parser.add_argument(
        '--output', '-o',
        choices=['terminal', 'json', 'html'],
        default='terminal',
        help='Output format (default: terminal)'
    )
    scan_parser.add_argument(
        '--output-file', '-O',
        default=None,
        help='Write report to file instead of stdout (useful for HTML output)'
    )
    scan_parser.add_argument(
        '--timeout',
        type=int,
        default=30,
        help='Session timeout in minutes (default: 30)'
    )
    scan_parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show detailed feature vectors and heuristic rule breakdown'
    )
    scan_parser.add_argument(
        '--json-pretty', '-j',
        action='store_true',
        help='Output colored JSON for terminal reading (human-friendly)'
    )
    scan_parser.add_argument(
        '--watch', '-w',
        action='store_true',
        help='Continuously monitor log file for new bot traffic (tails the file)'
    )
    
    # probe command
    probe_parser = subparsers.add_parser(
        'probe',
        help='Probe a live URL for bot detection signals'
    )
    probe_parser.add_argument(
        'url',
        help='URL to probe (e.g., https://example.com)'
    )
    probe_parser.add_argument(
        '--count', '-n',
        type=int,
        default=3,
        help='Number of probes to send (default: 3, more = better timing analysis)'
    )
    probe_parser.add_argument(
        '--delay', '-d',
        type=float,
        default=0.5,
        help='Delay between probes in seconds (default: 0.5)'
    )
    probe_parser.add_argument(
        '--method',
        choices=['GET', 'POST', 'HEAD', 'OPTIONS'],
        default='GET',
        help='HTTP method (default: GET)'
    )
    probe_parser.add_argument(
        '--threshold', '-t',
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f'Bot score threshold (default: {DEFAULT_THRESHOLD})'
    )
    probe_parser.add_argument(
        '--model', '-m',
        default=DEFAULT_MODEL_PATH,
        help='Path to pre-trained model file'
    )
    probe_parser.add_argument(
        '--output', '-o',
        choices=['terminal', 'json', 'html'],
        default='terminal',
        help='Output format (default: terminal)'
    )
    probe_parser.add_argument(
        '--output-file', '-O',
        default=None,
        help='Write report to file instead of stdout'
    )
    probe_parser.add_argument(
        '--timeout',
        type=float,
        default=10.0,
        help='Request timeout in seconds (default: 10)'
    )
    probe_parser.add_argument(
        '--no-verify-ssl',
        action='store_true',
        help='Disable SSL certificate verification'
    )
    probe_parser.add_argument(
        '--user-agent',
        default=None,
        help='Custom User-Agent header (default: Chrome)'
    )
    probe_parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show detailed feature vectors and heuristic rule breakdown'
    )
    probe_parser.add_argument(
        '--json-pretty', '-j',
        action='store_true',
        help='Output colored JSON for terminal reading (human-friendly)'
    )
    
    # info command
    subparsers.add_parser(
        'info',
        help='Show information about microguard'
    )
    
    args = parser.parse_args()
    
    if args.command == 'scan':
        # Watch mode: continuous monitoring
        if args.watch:
            from .watch import watch_logfile
            watch_logfile(
                filepath=args.logfile,
                fmt=args.format,
                threshold=args.threshold,
                model_path=args.model,
            )
            sys.exit(0)
        
        # Validate file exists
        if not os.path.exists(args.logfile):
            print(f"❌ Error: Log file not found: {args.logfile}", file=sys.stderr)
            sys.exit(1)
        
        # Run scan
        results = scan_logfile(
            filepath=args.logfile,
            fmt=args.format,
            threshold=args.threshold,
            model_path=args.model,
            timeout_minutes=args.timeout,
        )
        
        # Print report
        if args.verbose:
            from .report import format_verbose
            content = format_verbose(results)
            if args.output_file:
                with open(args.output_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"📄 Report saved to {args.output_file}", file=sys.stderr)
            else:
                print(content)
        elif args.json_pretty:
            from .report import format_json_pretty
            print(format_json_pretty(results))
        elif args.output_file:
            from .report import format_html, format_json, format_terminal
            if args.output == 'html':
                content = format_html(results)
            elif args.output == 'json':
                content = format_json(results)
            else:
                content = format_terminal(results)
            
            with open(args.output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"📄 Report saved to {args.output_file}", file=sys.stderr)
        else:
            print_report(results, fmt=args.output)
        
        # Exit code: 1 if bots detected above threshold
        if results['bot_rate'] > 0.1:
            sys.exit(1)
        else:
            sys.exit(0)
    
    elif args.command == 'probe':
        from .scanner import format_probe_report, probe_and_analyze
        
        # Load model
        model = None
        model_available = os.path.exists(args.model)
        if model_available:
            try:
                model = BotDetector(args.model)
            except Exception as e:  # noqa: BLE001 — model load is best-effort, falls back to heuristics
                print(f"⚠️  Could not load model: {e}", file=sys.stderr)
        
        # Run probe analysis
        results = probe_and_analyze(
            url=args.url,
            model=model,
            count=args.count,
            delay=args.delay,
            threshold=args.threshold,
            verbose=True,
        )
        
        # Format and output
        if args.verbose:
            from .scanner import format_probe_verbose
            content = format_probe_verbose(results)
        elif args.json_pretty:
            from .report import format_json_pretty
            content = format_json_pretty(results)
        elif args.output == 'json':
            content = json.dumps(results, indent=2, default=str)
        elif args.output == 'html':
            from .scanner import format_probe_html
            content = format_probe_html(results)
        else:
            content = format_probe_report(results)
        
        if args.output_file:
            with open(args.output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"📄 Report saved to {args.output_file}", file=sys.stderr)
        else:
            print(content)
        
        # Exit code: 1 if bot detected
        if results['label'] == 'bot':
            sys.exit(1)
        else:
            sys.exit(0)
    
    elif args.command == 'info':
        print("🔍 Microguard v0.1.0")
        print("   Bot Traffic Audit Tool powered by micrograd")
        print()
        print("   Usage:")
        print("     microguard scan <logfile>    Scan a log file for bot traffic")
        print("     microguard probe <url>       Probe a live URL for bot signals")
        print("   Docs:  https://github.com/yourusername/microguard")
        print()
        print("   Features:")
        print("   • 19 HTTP-level features for bot detection")
        print("   • micrograd neural network (85 parameters, ~1.8KB)")
        print("   • Heuristic rules + ML model combined scoring")
        print("   • Live URL probing with timing analysis")
        print("   • Streaming log processing (handles large files)")
        print("   • HTML report output for sharing with your team")
        print("   • Zero external dependencies (beyond micrograd)")
    
    else:
        # T2: Auto-sample on first run — scan bundled sample log
        sample_path = os.path.join(
            os.path.dirname(__file__), '..', 'data', 'sample_access.log'
        )
        if os.path.exists(sample_path):
            print("🔍 Microguard — Bot Traffic Audit Tool")
            print("   Powered by micrograd")
            print()
            print("   No command specified. Running sample scan...")
            print("   (Scan your own logs with: microguard scan <logfile>)")
            print()
            
            results = scan_logfile(
                filepath=sample_path,
                fmt='auto',
                threshold=DEFAULT_THRESHOLD,
                model_path=DEFAULT_MODEL_PATH,
            )
            print_report(results, fmt='terminal')
            
            print("\n─── What just happened? ───")
            print("   Microguard analyzed a sample log file for bot traffic.")
            print("   The model scored each session 0.0 (human) to 1.0 (bot).")
            print()
            print("   Next steps:")
            print("   • Scan your own logs:  microguard scan access.log")
            print("   • Probe a live URL:    microguard probe https://yourapi.com")
            print("   • See all options:     microguard --help")
            print()
        else:
            parser.print_help()
        sys.exit(0)


if __name__ == '__main__':
    main()
