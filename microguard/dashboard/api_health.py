"""Health endpoint — what the dashboard can and cannot see right now."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request

from .. import __version__
from ..model import DEFAULT_MODEL_PATH

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    """Report the two states that silently degrade every decision.

    A missing model means every score is heuristics-only, and no Redis means
    the live tab has nothing to show. Both are survivable and neither raises,
    so they have to be reported rather than inferred.
    """
    return {
        "version": __version__,
        "model_loaded": os.path.exists(DEFAULT_MODEL_PATH),
        "redis_connected": request.app.state.redis_connected,
    }
