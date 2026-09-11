"""Filesystem locations the dashboard is allowed to read."""

from __future__ import annotations

import os

# data/ sits beside the package, the same way DEFAULT_MODEL_PATH finds it.
DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data")
)


def resolve_within(directory: str, name: str) -> str | None:
    """Resolve `name` inside `directory`, or None if it escapes.

    A sample name arrives from the browser, so "../setup.py" and an absolute
    path are both things a caller will try. Resolve first, compare after —
    checking the raw string for ".." misses symlinks and encoded separators.
    """
    candidate = os.path.realpath(os.path.join(directory, name))
    root = os.path.realpath(directory)
    if os.path.commonpath([candidate, root]) != root:
        return None
    if candidate == root:
        return None
    return candidate
