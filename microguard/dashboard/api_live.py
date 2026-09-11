"""Live endpoints — counters, the decision ring, and the SSE feed.

Everything here reads from a DecisionRecorder. The dashboard never scores a
request itself; it reports what the check server or the middleware decided.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..events import DecisionRecorder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

MAX_EVENTS = 1000
POLL_INTERVAL_S = 2.0


async def decision_stream(
    recorder: DecisionRecorder,
    poll_interval: float = POLL_INTERVAL_S,
    _max_ticks: int | None = None,
) -> AsyncIterator[dict]:
    """Emit `stats` and `decision` SSE events as traffic arrives.

    Polls the recorder rather than subscribing to it: the decisions are written
    by a different process (the check server or the middleware), so there is no
    in-process callback to hang off, and a poll keeps the recorder Protocol to
    three methods.

    `_max_ticks` bounds the otherwise-infinite loop for tests, the same way
    watch_logfile's `_max_iterations` does. Don't use it from product code.
    """
    seen: set[tuple[float, str]] = set()
    ticks = 0
    while _max_ticks is None or ticks < _max_ticks:
        yield {"event": "stats", "data": json.dumps(recorder.stats())}

        # Oldest first, so a client rendering in arrival order sees the same
        # sequence the site did.
        for decision in reversed(recorder.recent(limit=MAX_EVENTS)):
            fingerprint = (decision.get("ts", 0.0), decision.get("ip", ""))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            yield {"event": "decision", "data": json.dumps(decision)}

        ticks += 1
        if _max_ticks is None or ticks < _max_ticks:
            await asyncio.sleep(poll_interval)


@router.get("/stats")
def stats(request: Request) -> dict:
    """Counters since the recorder started."""
    return request.app.state.recorder.stats()


@router.get("/events")
def events(request: Request, limit: int = Query(default=100, ge=1, le=MAX_EVENTS)) -> dict:
    """The most recent decisions, newest first."""
    return {"events": request.app.state.recorder.recent(limit=limit)}


@router.get("/stream")
async def stream(request: Request) -> EventSourceResponse:
    """Server-sent events: one `stats` event per tick, plus each new decision."""
    # sse_starlette closes the generator when the client goes away, so the
    # loop does not need its own disconnect check.
    return EventSourceResponse(decision_stream(request.app.state.recorder))


class ConfigUpdate(BaseModel):
    """A change to the live block threshold. None clears the override."""

    model_config = {"extra": "forbid"}

    block_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


@router.get("/config")
def read_config(request: Request) -> dict:
    """The live block threshold override, and whether it can be changed here."""
    config = request.app.state.runtime_config
    return {
        "block_threshold": config.block_threshold() if config is not None else None,
        "writable": bool(request.app.state.allow_config_writes and config is not None),
    }


@router.put("/config")
def write_config(request: Request, update: ConfigUpdate) -> dict:
    """Move the live block threshold, without restarting the check server.

    Off by default. This is a blocking control plane: a threshold of 0 blocks
    every visitor and a threshold of 1 blocks none of them, so the dashboard
    only offers it when the operator asked for it with --allow-config-writes.
    """
    if not request.app.state.allow_config_writes:
        raise HTTPException(
            status_code=403,
            detail="Config writes are disabled. Start with --allow-config-writes.",
        )

    config = request.app.state.runtime_config
    if config is None:
        raise HTTPException(
            status_code=503,
            detail="No shared config store. The dashboard needs the same Redis "
            "the live path uses.",
        )

    previous = config.block_threshold()
    config.set_block_threshold(update.block_threshold)
    # Loud on purpose: this changes who gets blocked on a live site.
    logger.warning(
        "live block_threshold changed: %s -> %s", previous, update.block_threshold
    )
    return {"block_threshold": update.block_threshold, "writable": True}
