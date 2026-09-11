"""Tests for LiveSession, the live path's in-memory session shape.

`LiveSession.add_request` had no test at all. The suite's InMemoryStore double
appends to `session.requests` directly and sets the clock itself, so the real
method was never called — which is unfortunate, because the invariant it
maintains is the one its own docstring warns about.
"""

import time
from datetime import datetime, timezone

from microguard.live.state import LiveSession, SessionStateStore
from microguard.parser import LogEntry


def _entry(url="/api/items"):
    return LogEntry(
        ip="10.0.0.1",
        timestamp=datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc),
        method="GET",
        url=url,
        status=0,
        size=0,
        referer="",
        user_agent="Mozilla/5.0",
    )


class TestAddRequest:
    def test_appends_the_request(self):
        session = LiveSession(ip="10.0.0.1")

        session.add_request(_entry("/a"))
        session.add_request(_entry("/b"))

        assert session.request_count == 2
        assert [e.url for e in session.requests] == ["/a", "/b"]

    def test_stamps_float_epochs_not_datetimes(self):
        """The clock must be float epochs.

        features.Session keeps the same two attribute names as datetimes. If
        this stamped a datetime, `.duration` would return a timedelta and
        labeler.py's MIN_RATE_WINDOW_S comparison would raise. The two shapes
        only duck-type because both expose a float duration.
        """
        session = LiveSession(ip="10.0.0.1")

        session.add_request(_entry())

        assert isinstance(session.start_time, float)
        assert isinstance(session.end_time, float)
        assert isinstance(session.duration, float)

    def test_first_request_sets_the_start_and_does_not_move_it(self):
        session = LiveSession(ip="10.0.0.1")

        session.add_request(_entry())
        first_start = session.start_time
        time.sleep(0.01)
        session.add_request(_entry())

        assert session.start_time == first_start
        assert session.end_time > first_start

    def test_duration_grows_with_the_session(self):
        session = LiveSession(ip="10.0.0.1")

        session.add_request(_entry())
        time.sleep(0.02)
        session.add_request(_entry())

        assert session.duration >= 0.02


class TestEmptySession:
    def test_counts_zero(self):
        assert LiveSession(ip="10.0.0.1").request_count == 0

    def test_duration_is_zero_rather_than_raising(self):
        assert LiveSession(ip="10.0.0.1").duration == 0.0

    def test_carries_the_user_agent_it_was_built_with(self):
        assert LiveSession(ip="10.0.0.1", user_agent="curl/8.0").user_agent == "curl/8.0"


class TestProtocolConformance:
    def test_the_in_memory_double_satisfies_the_store_protocol(self, store):
        """SessionStateStore is @runtime_checkable, so this is a real check
        that the test double has not drifted from the interface."""
        assert isinstance(store, SessionStateStore)
