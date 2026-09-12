"""Capture real API payloads as TypeScript test fixtures.

The zod schemas in src/api/schemas.ts are a hand-written mirror of shapes the
Python side produces. Mirrors drift. These fixtures are the real thing, and
gui/src/api/schemas.test.ts parses every one of them, so a shape change in
scan_logfile() or LiveScorer fails the frontend build instead of rendering
undefined in a table.

Run from the repo root:  python gui/scripts/capture_fixtures.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient

from microguard.cli import scan_logfile
from microguard.dashboard.app import create_app
from microguard.events import InMemoryDecisionRecorder
from microguard.live.scorer import LiveScorer
from microguard.live.state import LiveSession, SessionSnapshot, SessionStateStore
from microguard.parser import LogEntry, parse_file

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "api", "__fixtures__")


class _MemoryStore(SessionStateStore):
    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}

    def record_request(self, ip, user_agent, entry, ttl_seconds=None):
        session = self._sessions.setdefault(ip, LiveSession(ip=ip, user_agent=user_agent))
        # Deliberately NOT session.add_request(): that stamps time.time(), so
        # duration — and every timing feature derived from it, hence
        # model_score — changed on every capture and the fixtures could never
        # match. Anchoring the clock to the log's own timestamps makes a
        # capture reproducible on any machine. Capture-only; the real store
        # uses wall-clock, which is what the live path needs.
        session.requests.append(entry)
        session.start_time = session.requests[0].timestamp.timestamp()
        session.end_time = session.requests[-1].timestamp.timestamp()
        # No signals: a capture must not depend on a refresher having run.
        return SessionSnapshot(session=session)

    def delete(self, ip):
        self._sessions.pop(ip, None)


# Wall-clock values would make every capture differ from the last, which would
# make the CI drift check useless. They are pinned; the schemas only care about
# the type.
FIXED_TIMESTAMP = 1_700_000_000.0
# Decision ids are uuid4, so they differ on every capture for the same reason.
# Pinned to a well-formed hex id: the schema checks the type, and a recognisable
# placeholder makes it obvious in the fixture that this value is synthetic.
FIXED_DECISION_ID = "0" * 32


def _stabilize(payload):
    """Replace values that change on every run with fixed ones, recursively.

    Anything non-deterministic here makes the CI drift check permanently red:
    it recaptures and diffs, so a field that differs every run can never match
    the committed fixture. Wall-clock stamps did this once; decision ids are
    the same problem with a different type.
    """
    if isinstance(payload, dict):
        return {
            key: FIXED_TIMESTAMP
            if key in ("ts", "started_at") and isinstance(value, (int, float))
            else FIXED_DECISION_ID
            if key == "id" and isinstance(value, str)
            else _stabilize(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_stabilize(item) for item in payload]
    return payload


def _write(name: str, payload) -> None:
    path = os.path.join(FIXTURE_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_stabilize(payload), handle, indent=2, default=str)
        handle.write("\n")
    print(f"wrote {path}")


def main() -> None:
    os.makedirs(FIXTURE_DIR, exist_ok=True)

    recorder = InMemoryDecisionRecorder()
    client = TestClient(create_app(recorder=recorder))

    _write("health", client.get("/api/health").json())
    _write("scan-samples", client.get("/api/scan/samples").json())

    scan = client.post("/api/scan", json={"sample": "sample_access.log"}).json()
    _write("scan", scan)
    # The error variant drops threshold/model_used/integration_count/summary
    # and adds `error` (cli.py:62). It is only reachable through the engine —
    # the endpoint 404s an unknown sample — but the UI still has to render it.
    _write("scan-error", scan_logfile("does-not-exist.log"))

    _write("model", client.get("/api/model").json())
    _write(
        "model-evaluation",
        client.post("/api/model/evaluate", json={"dataset": "holdout", "threshold": 0.5}).json(),
    )

    # Real decisions, produced by the live scorer rather than hand-written.
    scorer = LiveScorer(_MemoryStore(), recorder=recorder, block_threshold=0.85)
    entries = list(parse_file("data/sample_access.log"))
    for entry in entries:
        scorer.score_request(entry)
    scorer.score_request(
        LogEntry(
            ip="203.0.113.9",
            timestamp=entries[0].timestamp,
            method="GET",
            url="/wp-admin/setup-config.php",
            status=0,
            size=0,
            referer="",
            user_agent="curl/8.0",
        )
    )

    _write("live-stats", client.get("/api/live/stats").json())
    _write("live-events", client.get("/api/live/events?limit=20").json())
    _write("live-config", client.get("/api/live/config").json())


if __name__ == "__main__":
    main()
