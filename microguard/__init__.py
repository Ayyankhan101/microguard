"""Microguard — Bot Traffic Audit Tool powered by micrograd.

A tiny, zero-dependency CLI tool that detects malicious bot traffic
in your API access logs using a micrograd neural network.
"""

__version__ = "0.1.0"
__author__ = "Microguard"

from .parser import parse_file, parse_string, detect_format, LogEntry
from .features import extract_features, group_into_sessions, FEATURE_NAMES, Session
from .labeler import label_session, label_entries
from .model import BotDetector
from .report import format_terminal, format_json, format_html, print_report
from .scanner import probe_url, probe_and_analyze, extract_probe_features
from .cli import scan_logfile

__all__ = [
    'parse_file',
    'parse_string', 
    'detect_format',
    'LogEntry',
    'extract_features',
    'group_into_sessions',
    'FEATURE_NAMES',
    'Session',
    'label_session',
    'label_entries',
    'BotDetector',
    'format_terminal',
    'format_json',
    'format_html',
    'print_report',
    'probe_url',
    'probe_and_analyze',
    'extract_probe_features',
    'scan_logfile',
]
