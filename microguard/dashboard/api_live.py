"""Live endpoints — counters, the decision ring, and the SSE feed.

Everything here reads from a DecisionRecorder. The dashboard never scores a
request itself; it reports what the check server or the middleware decided.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..events import DecisionRecorder
from ..signals import KNOWN_SIGNAL_SOURCES
from ..training.online_update import record_correction

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
    """A change to live blocking configuration.

    Both fields are absolute, not deltas: `block_threshold: null` clears the
    override, and `promoted_signals: []` returns every signal to observe-only.
    A PUT that omits a field leaves it alone.
    """

    model_config = {"extra": "forbid"}

    block_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    promoted_signals: list[str] | None = Field(default=None)


@router.get("/config")
def read_config(request: Request) -> dict:
    """The live block threshold override, and whether it can be changed here."""
    config = request.app.state.runtime_config
    return {
        "block_threshold": config.block_threshold() if config is not None else None,
        "promoted_signals": (
            sorted(config.promoted_signals()) if config is not None else []
        ),
        "known_signals": sorted(KNOWN_SIGNAL_SOURCES),
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

    if update.promoted_signals is not None:
        try:
            config.set_promoted_signals(set(update.promoted_signals))
        except ValueError as exc:
            # A typo must fail here rather than leaving the operator believing
            # a signal is enforced while it quietly is not.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Promotion is the moment a signal stops being a measurement and starts
        # blocking real visitors. It belongs in the log at the same volume as
        # a threshold change.
        logger.warning("promoted signals changed to: %s", sorted(update.promoted_signals))

    return {
        "block_threshold": update.block_threshold,
        "promoted_signals": sorted(config.promoted_signals()),
        "known_signals": sorted(KNOWN_SIGNAL_SOURCES),
        "writable": True,
    }


class FeedbackSubmission(BaseModel):
    """An operator saying a recorded decision was wrong.

    `label` is what the session ACTUALLY was, not what microguard said. That
    reads more naturally at the click site ("this was a human") and leaves no
    room for the off-by-one an "is_wrong" boolean invites.
    """

    model_config = {"extra": "forbid"}

    decision_id: str = Field(min_length=1, max_length=64)
    label: Literal["bot", "human"]


@router.post("/feedback")
def submit_feedback(request: Request, submission: FeedbackSubmission) -> dict:
    """Record a correction against one decision.

    Open by default, unlike the config writes above. A recorded correction
    changes nothing until someone deliberately retrains, and the safety rails
    in online_update refuse thin or skewed data at that point -- so the gate
    belongs there, not here. Gating collection instead would leave the button
    dark on a default install, and a retrain needs 50 corrections before it
    will run at all.
    """
    deployment_id = getattr(request.app.state, "deployment_id", None)
    if not deployment_id:
        raise HTTPException(
            status_code=503,
            detail="Corrections are per-deployment. Start the dashboard with "
            "--deployment-id to record them.",
        )

    recorder = request.app.state.recorder
    decision = next(
        (d for d in recorder.recent(MAX_EVENTS) if d.get("id") == submission.decision_id),
        None,
    )
    if decision is None:
        raise HTTPException(
            status_code=404,
            detail="No such decision. The feed keeps the most recent "
            f"{MAX_EVENTS}; older ones cannot be corrected.",
        )

    features = decision.get("features")
    if not features:
        raise HTTPException(
            status_code=422,
            detail="That decision carries no features - nothing was scored for "
            "it, so there is nothing to train on. Fail-open rows look like this.",
        )

    try:
        record_correction(
            deployment_id=deployment_id,
            decision_id=submission.decision_id,
            features=features,
            confirmed_label=1.0 if submission.label == "bot" else 0.0,
            feedback_dir=getattr(request.app.state, "feedback_dir", None),
        )
    except OSError as exc:
        # Surfaced, never swallowed: an operator who clicked and saw nothing
        # happen would click again, and the correction would still be lost.
        logger.exception("could not record correction")
        raise HTTPException(status_code=500, detail=f"Could not record: {exc}") from exc

    return {"recorded": True, "decision_id": submission.decision_id}
