"""Live request scoring entrypoint — the ONE place real-time scoring happens.

Called by the HTTP server (Phase 4) and ASGI/WSGI middleware (Phase 5).
Loads the trained model once at startup, scores individual requests
against accumulated session history.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ..events import DecisionRecorder
from ..features import extract_features
from ..labeler import label_session
from ..model import DEFAULT_MODEL_PATH, BotDetector
from ..parser import LogEntry
from ..scoring import BLOCK_THRESHOLD_DEFAULT, compute_combined_score
from ..signals import Signals
from .state import SessionStateStore

logger = logging.getLogger(__name__)



def _load_model(model_path: str | Path | None = None) -> BotDetector | None:
    """Load the trained model, returning None if it cannot be loaded.

    A None here is not benign: score_request falls back to model_score = 0.0,
    which collapses the blend to the heuristic confidence alone and takes the
    model out of every live blocking decision. That went unnoticed once already
    because this function searched for a filename that never existed in the
    repo and said nothing when it came up empty. Every failure path now logs.
    """
    if model_path is None:
        model_path = DEFAULT_MODEL_PATH
    if not Path(model_path).exists():
        logger.warning(
            "no model at %s - live scoring will run on heuristics only", model_path
        )
        return None
    try:
        detector = BotDetector()
        detector.load(str(model_path))
        return detector
    except (FileNotFoundError, OSError, KeyError, ValueError, json.JSONDecodeError):
        logger.warning(
            "model at %s failed to load - live scoring will run on heuristics only",
            model_path,
            exc_info=True,
        )
        return None


def fail_open_result(ip: str = "") -> dict:
    """The decision payload for a request that could not be scored.

    One builder, not one copy per entrypoint. The keys must match a real
    decision exactly so downstream consumers never special-case a failure --
    `model_loaded` False and the reason string are what say what happened.
    Two hand-maintained copies of this dict drifted the moment a key was added
    to the real decision, and a fail-open payload missing a key the dashboard
    reads fails during an outage, which is the worst time to find it.
    """
    return {
        "ip": ip,
        "label": "human",
        "score": 0.0,
        "model_score": 0.0,
        "heuristic_label": "unknown",
        "heuristic_confidence": 0.0,
        "heuristic_reason": "scoring unavailable",
        "reason": "scoring unavailable",
        "request_count": 0,
        "duration": 0.0,
        "model_loaded": False,
        # No threshold was consulted, and saying otherwise would let a
        # dashboard plot a bar this decision never met.
        "block_threshold": None,
        "signals": _signal_summary(None),
    }


def _signal_summary(signals: Signals | None) -> dict:
    """The signal state worth keeping on a decision record.

    `promoted` is included because the same signal value means different
    things depending on whether it was allowed to decide, and a record that
    omitted it could not explain its own verdict later.
    """
    if signals is None or not signals.resolved:
        return {"resolved": False}
    return {
        "resolved": True,
        "tor_exit": signals.tor_exit,
        "hosting_range": signals.hosting_range,
        "abuse_score": signals.abuse_score,
        "promoted": sorted(signals.promoted),
    }


class LiveScorer:
    """Scores requests against live session state.

    Usage:
        store = RedisSessionStateStore(redis_client)
        scorer = LiveScorer(store)
        result = scorer.score_request(entry)
        if result["label"] == "bot":
            return 403
        return 200
    """

    def __init__(
        self,
        store: SessionStateStore,
        model_path: str | Path | None = None,
        block_threshold: float = BLOCK_THRESHOLD_DEFAULT,
        session_ttl: int = 1800,
        short_circuit_label: str = "automated-integration",
        recorder: DecisionRecorder | None = None,
        threshold_source: Callable[[], float | None] = lambda: None,
        promoted_source: Callable[[], frozenset[str]] = frozenset,
    ):
        self._store = store
        self._model = _load_model(model_path)
        self._block_threshold = block_threshold
        self._session_ttl = session_ttl
        self._short_circuit_label = short_circuit_label
        self._recorder = recorder
        self._threshold_source = threshold_source
        self._promoted_source = promoted_source

    @property
    def model_loaded(self) -> bool:
        """Whether scores include a model contribution at all."""
        return self._model is not None

    def score_request(self, entry: LogEntry) -> dict:
        """Score a single request against accumulated session state.

        Returns the full decision, not just the verdict:

            {
                "ip": str,
                "label": "bot" | "human",
                "score": float,           # the blended score the decision used
                "model_score": float,     # what the model alone said
                "heuristic_label": str,   # what the rules alone said
                "heuristic_confidence": float,
                "heuristic_reason": str,
                "request_count": int,
                "duration": float,        # seconds spanned by retained history
                "model_loaded": bool,
            }

        The breakdown is the point. A single blended float tells an operator
        that a customer was blocked but not whether the rules or the model
        drove it, which is exactly what you need to tune a threshold. It is
        also how a dead model stays visible: model_score pinned at 0.0 across
        every response is a symptom you can see.
        """
        # One atomic call: append, cap, refresh TTL, and read back the session
        # to score. The old get/mutate/set pair lost concurrent appends from
        # the same actor, which undercounted request_count under exactly the
        # load the rate and timing rules are there to catch.
        snapshot = self._store.record_request(
            entry.ip,
            entry.user_agent,
            entry,
            ttl_seconds=self._session_ttl,
        )
        session = snapshot.session
        signals = self._promote(snapshot.signals)

        h_label, h_conf, h_reason = label_session(session, signals)  # type: ignore[arg-type]

        # Short-circuit for automated integrations
        if h_label == self._short_circuit_label:
            return self._result(
                session,
                label="human",
                score=0.0,
                model_score=0.0,
                h_label=h_label,
                h_conf=h_conf,
                h_reason=f"automated-integration (not blocked): {h_reason}",
            )

        # Features are needed by the model AND by the recorder, which keeps
        # them so an operator's later correction trains on the vector the
        # decision was actually made from. Computing them is roughly 2ms and is
        # the dominant per-request cost, so skip it when nothing consumes it:
        # no model and no recorder means no consumer.
        features = None
        if self._model is not None or self._recorder is not None:
            features = extract_features(session)  # type: ignore[arg-type]
        model_score = self._model.predict(features) if self._model is not None and features else 0.0

        threshold = self._threshold()
        combined = compute_combined_score(h_label, h_conf, model_score)
        # A score sitting exactly on the bar has not cleared it. At a 0.5
        # threshold that number is the neutral verdict itself ("no strong
        # signals either way" caps at exactly 0.5), and spec AC#7 says an
        # unknown visitor with no history is allowed by default. Strict `>`
        # also makes threshold 1.0 a real never-block escape hatch.
        label = "bot" if combined > threshold else "human"

        return self._result(
            session,
            label=label,
            score=combined,
            threshold=threshold,
            model_score=model_score,
            h_label=h_label,
            h_conf=h_conf,
            h_reason=h_reason,
            signals=signals,
        )

    def _promote(self, signals: Signals) -> Signals:
        """Attach the deployment's promotion list to this actor's signals.

        Promotion is deployment configuration, not per-actor data, so the store
        does not carry it. Read per request, like the threshold, so an operator
        can promote a signal from the dashboard without restarting and dropping
        every in-flight session. A failing source promotes nothing, which keeps
        an unreachable config store from silently enforcing a signal nobody
        approved.
        """
        if not signals.resolved:
            return signals
        try:
            promoted = self._promoted_source()
        except Exception:
            logger.exception("promotion source failed, treating every signal as observe-only")
            return signals
        return replace(signals, promoted=promoted) if promoted else signals

    def _threshold(self) -> float:
        """The threshold this request is judged against.

        Read per request so an operator can move it from the dashboard without
        restarting and dropping every in-flight session. A source that fails
        falls back to the configured value rather than changing the verdict:
        an unreachable config store must not silently start blocking everyone
        or stop blocking anyone.
        """
        try:
            override = self._threshold_source()
        except Exception:
            logger.exception("threshold source failed, using configured threshold")
            return self._block_threshold
        return self._block_threshold if override is None else override

    def _result(
        self,
        session,
        *,
        label: str,
        score: float,
        model_score: float,
        h_label: str,
        h_conf: float,
        h_reason: str,
        threshold: float | None = None,
        signals: Signals | None = None,
    ) -> dict:
        """Assemble the decision payload. One place, so the short-circuit and
        the scored path cannot report different shapes — and so recording
        cannot miss one of them."""
        result = {
            "ip": session.ip,
            "label": label,
            "score": score,
            "model_score": model_score,
            "heuristic_label": h_label,
            "heuristic_confidence": h_conf,
            "heuristic_reason": h_reason,
            "request_count": session.request_count,
            "duration": session.duration,
            "model_loaded": self.model_loaded,
            # Which bar this decision was actually judged against — without it
            # a dashboard cannot tell a changed threshold from a changed score.
            "block_threshold": self._block_threshold if threshold is None else threshold,
            # Kept for callers reading the old four-key shape.
            "reason": h_reason,
            # The observe half of observe-until-promoted: an unpromoted signal
            # decides nothing, but it still travels into the record so an
            # operator can see what it would have done before enforcing it.
            "signals": _signal_summary(signals),
        }
        self._record(result)
        return result

    def _record(self, result: dict) -> None:
        """Tee the decision to the dashboard recorder, if there is one.

        Every exception is swallowed. Recording exists so an operator can see
        what happened; blocking exists so the site stays up. nginx turns any
        non-2xx/401/403 from /check into a 500 for the visitor, so a Redis blip
        in the recorder must not become an outage — the same fail-open reasoning
        as server.py's scoring guard.
        """
        if self._recorder is None:
            return
        try:
            self._recorder.record(result)
        except Exception:
            logger.exception("decision recording failed, continuing")
