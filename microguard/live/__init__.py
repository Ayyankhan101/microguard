"""Live real-time bot detection package.

Requires Redis for session state storage:
    pip install microguard[live]
"""

try:
    import redis as _redis  # noqa: F401
except ImportError:  # pragma: no cover - fires only on an install without the extra
    raise ImportError(
        "Real-time mode requires the 'live' extra. "
        "Install with: pip install microguard[live]"
    ) from None
