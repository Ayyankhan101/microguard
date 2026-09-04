"""Microguard — Bot Traffic Audit Tool powered by micrograd.

A tiny, zero-dependency CLI tool that detects malicious bot traffic
in your API access logs using a micrograd neural network.
"""

__version__ = "0.1.0"
__author__ = "Microguard"

from .cli import scan_logfile
from .features import FEATURE_NAMES, Session, extract_features, group_into_sessions
from .labeler import label_entries, label_session
from .model import BotDetector
from .parser import LogEntry, detect_format, parse_file, parse_string
from .report import format_html, format_json, format_terminal, print_report
from .scanner import extract_probe_features, probe_and_analyze, probe_url

__all__ = [
    'FEATURE_NAMES',
    'BotDetector',
    'LogEntry',
    'Session',
    'detect_format',
    'extract_features',
    'extract_probe_features',
    'format_html',
    'format_json',
    'format_terminal',
    'group_into_sessions',
    'label_entries',
    'label_session',
    'parse_file',
    'parse_string',
    'print_report',
    'probe_and_analyze',
    'probe_url',
    'scan_logfile',
]
