"""Host-agnostic handlers for the two public fingerprint routes.

One implementation, three hosts. `microguard serve` exposes these through a
second, non-internal nginx location, and the ASGI and WSGI middleware expose
them directly, because the epic requires both deployment modes to carry the
same capability. Writing the handler three times is how three implementations
drift; test_fp_contract.py asserts they do not.

Everything here answers 200. These routes are public, unauthenticated, and
non-critical: a rejection is silent so an attacker cannot probe for a bindable
IP, and a failure is silent so a microguard problem never surfaces as an error
on someone else's page.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .fingerprint import record_fingerprint

logger = logging.getLogger(__name__)

# A SHA-256 hex digest inside a small JSON envelope is about 100 bytes. The cap
# is generous and still small enough that nothing large is ever read from an
# unauthenticated public route.
MAX_FP_BODY_BYTES = 1024

# One fixed response for every outcome. See the module docstring.
_ACK = b'{"ok":true}'

# Both spellings. nginx may or may not strip the location prefix depending on
# whether the proxy_pass target carries a trailing path, and an operator who
# gets that subtly wrong should not get a silently dead fingerprint pipeline.
FP_PATHS = frozenset({"/fp", "/microguard/fp"})
SCRIPT_PATHS = frozenset({"/fingerprint.js", "/microguard/fingerprint.js"})

_SCRIPT_PATH = Path(__file__).parent / "static" / "fingerprint.js"
_script_cache: bytes | None = None


def fingerprint_script() -> tuple[bytes, str]:
    """The client probe and its content type. Read once, then cached."""
    global _script_cache
    if _script_cache is None:
        _script_cache = _SCRIPT_PATH.read_bytes()
    return _script_cache, "application/javascript; charset=utf-8"


def handle_fp_post(client, ip: str, body: bytes) -> tuple[int, bytes]:
    """Record a submitted fingerprint. Always (200, ack)."""
    if len(body) > MAX_FP_BODY_BYTES:
        # Checked before parse: nothing large is ever handed to a JSON decoder
        # from an unauthenticated route.
        return 200, _ACK

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return 200, _ACK
    if not isinstance(payload, dict):
        return 200, _ACK

    submitted = payload.get("fingerprint_hash")
    if not isinstance(submitted, str):
        return 200, _ACK

    try:
        record_fingerprint(client, ip, submitted)
    except Exception:
        # The route lives in the process nginx depends on. A Redis blip must
        # cost one fingerprint, not a 500 on the visitor's page.
        logger.exception("fingerprint recording failed, ignoring submission")
    return 200, _ACK
