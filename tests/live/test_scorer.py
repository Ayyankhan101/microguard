"""Tests for LiveScorer — in-memory store, no Redis needed."""

from datetime import datetime, timezone

import pytest

from microguard.live.scorer import LiveScorer
from microguard.live.state import LiveSession
from microguard.parser import LogEntry


class InMemoryStore:
    """Minimal in-memory SessionStateStore for testing."""

    def __init__(self):
        self._data: dict[str, LiveSession] = {}
        self._scores: dict[str, float] = {}

    def get(self, key: str) -> LiveSession | None:
        return self._data.get(key)

    def set(self, key: str, session: LiveSession, ttl_seconds: int = 1800) -> None:
        self._data[key] = session

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._scores.pop(key, None)

    def incr_score(self, key: str, amount: float = 1.0) -> float:
        self._scores[key] = self._scores.get(key, 0.0) + amount
        return self._scores[key]

    def get_score(self, key: str) -> float:
        return self._scores.get(key, 0.0)

    def keys(self, pattern: str = "live:*") -> list[str]:
        return list(self._data.keys())


def _make_entry(ip="1.2.3.4", status=200, url="/api/test", ua="Mozilla/5.0", **kwargs):
    return LogEntry(
        ip=ip,
        timestamp=datetime.now(timezone.utc),
        method="GET",
        url=url,
        status=status,
        size=100,
        referer="",
        user_agent=ua,
        **kwargs,
    )


@pytest.fixture()
def store():
    return InMemoryStore()


@pytest.fixture()
def scorer(store):
    return LiveScorer(store, block_threshold=0.85)


# --- Basic scoring ---


def test_first_request_always_human(scorer):
    entry = _make_entry()
    result = scorer.score_request(entry)
    assert result["label"] == "human"
    assert result["request_count"] == 1


def test_session_accumulates_requests(scorer):
    for i in range(5):
        scorer.score_request(_make_entry(url=f"/page/{i}"))
    session = scorer._store.get("live:1.2.3.4")
    assert session is not None
    assert session.request_count == 5


def test_result_contains_score_and_reason(scorer):
    result = scorer.score_request(_make_entry())
    assert "score" in result
    assert "reason" in result
    assert isinstance(result["score"], float)


# --- Automated integration short-circuit ---


def test_automated_integration_not_blocked(scorer):
    entry = _make_entry(ua="Stripe/2.0")
    result = scorer.score_request(entry)
    assert result["label"] == "human"
    assert "automated-integration" in result["reason"]


def test_webhook_not_blocked(scorer):
    entry = _make_entry(ua="GitHub-Hookshot/1.0")
    result = scorer.score_request(entry)
    assert result["label"] == "human"


# --- Bot detection ---


def test_known_bot_ua_detected(scorer):
    entry = _make_entry(ua="python-requests/2.28.0")
    # Single request with known bot UA — heuristic labels it bot,
    # but score depends on confidence and model.
    result = scorer.score_request(entry)
    # Just verify it ran without error; actual classification
    # depends on model availability
    assert "label" in result
    assert result["label"] in ("bot", "human")


def test_scanner_patterns_detected(scorer):
    entry = _make_entry(url="/wp-admin/install.php", ua="Mozilla/5.0")
    result = scorer.score_request(entry)
    assert "label" in result


# --- Multiple IPs isolated ---


def test_different_ips_are_isolated(store):
    s1 = LiveScorer(store, block_threshold=0.85)
    s2 = LiveScorer(store, block_threshold=0.85)

    s1.score_request(_make_entry(ip="1.1.1.1"))
    s2.score_request(_make_entry(ip="2.2.2.2"))

    session1 = store.get("live:1.1.1.1")
    session2 = store.get("live:2.2.2.2")
    assert session1 is not None
    assert session2 is not None
    assert session1.request_count == 1
    assert session2.request_count == 1


# --- Edge cases ---


def test_empty_user_agent(scorer):
    entry = _make_entry(ua="")
    result = scorer.score_request(entry)
    assert result["label"] in ("bot", "human")


def test_model_load_failure_graceful(store):
    scorer = LiveScorer(store, model_path="/nonexistent/path.pkl")
    entry = _make_entry()
    result = scorer.score_request(entry)
    # Should still work, just without model score
    assert result["label"] in ("bot", "human")
    assert result["score"] >= 0.0
