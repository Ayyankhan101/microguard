"""Live request scoring entrypoint — the ONE place real-time scoring happens.

Called by the HTTP server (Phase 4) and ASGI/WSGI middleware (Phase 5).
Loads the trained model once at startup, scores individual requests
against accumulated session history.
"""

from __future__ import annotations

from pathlib import Path

from ..features import extract_features
from ..labeler import label_session
from ..model import BotDetector
from ..parser import LogEntry
from ..scoring import compute_combined_score
from .state import LiveSession, SessionStateStore


def _load_model(model_path: str | Path | None = None) -> BotDetector | None:
    """Load trained model, returning None if unavailable."""
    if model_path is None:
        candidates = [
            Path("data/bot_model.pkl"),
            Path(__file__).parent.parent.parent / "data" / "bot_model.pkl",
        ]
        for p in candidates:
            if p.exists():
                model_path = p
                break
    if model_path is None or not Path(model_path).exists():
        return None
    try:
        detector = BotDetector()
        detector.load(str(model_path))
        return detector
    except (FileNotFoundError, OSError, KeyError):
        return None


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
        block_threshold: float = 0.85,
        session_ttl: int = 1800,
        short_circuit_ua: str = "automated-integration",
    ):
        self._store = store
        self._model = _load_model(model_path)
        self._block_threshold = block_threshold
        self._session_ttl = session_ttl
        self._short_circuit_ua = short_circuit_ua

    def score_request(self, entry: LogEntry) -> dict:
        """Score a single request against accumulated session state.

        Returns:
            {
                "label": "bot" | "human",
                "score": float,
                "reason": str,
                "request_count": int,
            }
        """
        session = self._store.get(f"live:{entry.ip}")
        if session is None:
            session = LiveSession(ip=entry.ip, user_agent=entry.user_agent)

        session.add_request(entry)
        self._store.set(f"live:{entry.ip}", session, ttl_seconds=self._session_ttl)

        h_label, h_conf, h_reason = label_session(session)  # type: ignore[arg-type]

        # Short-circuit for automated integrations
        if h_label == self._short_circuit_ua:
            self._store.set(f"live:{entry.ip}", session, ttl_seconds=self._session_ttl)
            return {
                "label": "human",
                "score": 0.0,
                "reason": f"automated-integration (not blocked): {h_reason}",
                "request_count": session.request_count,
            }

        # Model prediction (if available)
        if self._model is not None:
            features = extract_features(session)  # type: ignore[arg-type]
            model_score = self._model.predict(features)
        else:
            model_score = 0.0

        combined = compute_combined_score(h_label, h_conf, model_score)
        label = "bot" if combined >= self._block_threshold else "human"

        session.last_score = combined
        self._store.set(f"live:{entry.ip}", session, ttl_seconds=self._session_ttl)

        return {
            "label": label,
            "score": combined,
            "reason": h_reason,
            "request_count": session.request_count,
        }
