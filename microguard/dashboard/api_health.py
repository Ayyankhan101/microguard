"""Health endpoint — what the dashboard can and cannot see right now."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Request

from .. import __version__
from ..model import DEFAULT_MODEL_PATH

try:
    from ..live.signals_runner import heartbeat
except ImportError:  # pragma: no cover - only on an install without the live extra
    # The dashboard has to run without redis-py; the signal panel then reports
    # "no redis" rather than the import taking the whole app down.
    heartbeat = None  # type: ignore[assignment]

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    """Report the two states that silently degrade every decision.

    A missing model means every score is heuristics-only, no Redis means the
    live tab has nothing to show, and a signal refresher nobody started means
    every threat-intel signal is silently absent. All three are survivable and
    none of them raises, so they have to be reported rather than inferred.
    """
    return {
        "version": __version__,
        "model_loaded": os.path.exists(DEFAULT_MODEL_PATH),
        "redis_connected": request.app.state.redis_connected,
        "signals": _signal_health(getattr(request.app.state, "redis", None)),
        # Corrections are per-deployment by definition. Reported here so
        # the dashboard can say why the control is unavailable instead of
        # letting every click discover it with a 503.
        "feedback_enabled": bool(getattr(request.app.state, "deployment_id", None)),
    }


def _signal_health(client) -> dict:
    """What the slow tier last did, from its heartbeat.

    An absent heartbeat means the refresher has never run against this Redis,
    which is reported as `running: false` rather than as healthy-with-no-data.
    A signal that is quietly never resolved looks exactly like a clean actor,
    and that is the failure this panel exists to make visible.
    """
    if client is None or heartbeat is None:
        return {"running": False, "reason": "no redis", "sources": []}
    try:
        beat = heartbeat(client)
    except Exception:  # noqa: BLE001 - health must never be the thing that breaks
        return {"running": False, "reason": "redis unreachable", "sources": []}
    if beat is None:
        return {"running": False, "reason": "never ran", "sources": []}
    return {
        "running": True,
        "age_seconds": max(0.0, time.time() - float(beat.get("ts", 0.0))),
        "resolved": beat.get("resolved", 0),
        "sources": beat.get("sources", []),
    }
