"""`microguard explain <ip>` — why this actor got the verdict it did.

The dashboard answers this better when you have a browser. This exists for the
case the operations runbook actually describes: a headless box, the dashboard
bound to loopback, and a decision you need to understand now. With external
signals in play the decision payload alone no longer explains a verdict — the
resolved signals and their promotion state are the missing half.

Read-only by construction. It reads the session back rather than recording a
request, because diagnosis must not change the thing being diagnosed.
"""

from __future__ import annotations

import json
from typing import cast

import redis

from ..labeler import label_session
from ..scoring import BLOCK_THRESHOLD_DEFAULT, compute_combined_score
from ..signals import EMPTY_SIGNALS, signals_from_payload
from .fingerprint import FP_PREFIX
from .redis_store import SESSION_PREFIX_DEFAULT, _session_from
from .signals_refresher import SIGNALS_PREFIX


def explain_actor(
    client: redis.Redis,
    ip: str,
    promoted: frozenset[str] = frozenset(),
    block_threshold: float = BLOCK_THRESHOLD_DEFAULT,
) -> str:
    """A human-readable account of this actor's current standing."""
    # redis-py types every command as a sync-or-async union. This client is
    # always sync (from_url without an async pool), same narrowing the store
    # and the check server already do.
    raw_entries = cast("list", client.lrange(f"{SESSION_PREFIX_DEFAULT}{ip}", 0, -1))
    if not raw_entries:
        return (
            f"{ip}: no live session.\n"
            "Nothing has been scored for this actor, or its session already "
            "expired (sessions slide out after their TTL).\n"
        )

    session = _session_from(ip, "", raw_entries)
    if session.requests:
        session.user_agent = session.requests[-1].user_agent

    raw_signals = cast("str | None", client.get(f"{SIGNALS_PREFIX}{ip}"))
    signals = EMPTY_SIGNALS
    if raw_signals:
        try:
            signals = signals_from_payload(json.loads(raw_signals))
        except json.JSONDecodeError:
            signals = EMPTY_SIGNALS
    # Fingerprint state lives in its own key, written by /fp rather than by
    # the refresher. `fp_resolved` is set either way: this command read the
    # key, so an absent hash means "none bound", not "never looked" -- the
    # distinction rule 1 turns on.
    from dataclasses import replace

    raw_fp = cast("str | None", client.get(f"{FP_PREFIX}{ip}"))
    bound_hash, shared = None, 0
    if raw_fp:
        try:
            fp_payload = json.loads(raw_fp)
            bound_hash = fp_payload.get("hash")
            shared = int(fp_payload.get("shared_ips", 0))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            bound_hash, shared = None, 0
    signals = replace(
        signals, fp_resolved=True, fingerprint_hash=bound_hash, shared_hash_ips=shared
    )
    if promoted:
        signals = replace(signals, promoted=promoted)

    label, confidence, reason = label_session(session, signals)  # type: ignore[arg-type]
    combined = compute_combined_score(label, confidence, 0.0)

    lines = [
        f"{ip}",
        f"  session:    {session.request_count} request(s) over {session.duration:.1f}s",
        f"  user agent: {session.user_agent or '(none)'}",
        f"  last paths: {', '.join(e.url for e in session.requests[-3:])}",
        "",
        f"  deciding rule: {reason}",
        f"  heuristic:     {label} at {confidence:.2f} confidence",
        # Heuristics only. The model contributes 60% of a live score and is
        # loaded by the check server, not here; saying otherwise would print a
        # number this command did not compute.
        f"  score (heuristic only, no model): {combined:.2f} against {block_threshold}",
        "",
    ]

    if not signals.resolved:
        lines.append("  signals: not resolved for this actor")
        lines.append("    Either `microguard signals` is not running, or it has")
        lines.append("    not reached this actor since its session began.")
    else:
        lines.append("  signals:")
        for name, value in (
            ("tor_exit", signals.tor_exit),
            ("hosting_range", signals.hosting_range),
            ("abuse_score", signals.abuse_score),
        ):
            source = {"tor_exit": "tor", "hosting_range": "hosting",
                      "abuse_score": "abuseipdb"}[name]
            state = "enforced" if signals.is_promoted(source) else "observe-only"
            lines.append(f"    {name}: {value}  ({state})")

    fp_state = "enforced" if signals.is_promoted("fingerprint") else "observe-only"
    lines.append("")
    lines.append(f"  fingerprint ({fp_state}):")
    if signals.fingerprint_hash:
        lines.append(f"    bound: {signals.fingerprint_hash[:16]}...")
        lines.append(f"    seen from {signals.shared_hash_ips} distinct IP(s) in the window")
    else:
        lines.append("    none bound for this actor")
        lines.append("    Either the page never embedded fingerprint.js, this is")
        lines.append("    an API client, or the origin is plain HTTP (the script")
        lines.append("    needs a secure context and will not run on one).")
    return "\n".join(lines) + "\n"
