"""Tests for the heuristic labeler."""

from datetime import datetime, timedelta, timezone

from microguard.features import Session
from microguard.labeler import label_entries, label_session
from microguard.parser import LogEntry


def _make_session(
    ip: str = "192.168.1.1",
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    num_requests: int = 10,
    timing: str = "variable",
    same_url: bool = False,
) -> Session:
    """Create a test session with configurable behavior."""
    session = Session(ip, user_agent)
    base_time = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)
    
    for i in range(num_requests):
        if timing == "uniform":
            # Uniform 50ms gaps (bot-like)
            ts = base_time + timedelta(milliseconds=i * 50)
        elif timing == "variable":
            # Variable gaps (human-like)
            ts = base_time + timedelta(seconds=i * 2 + (i % 3))
        else:  # "rapid"
            # Very rapid (< 1 second total)
            ts = base_time + timedelta(milliseconds=i * 10)
        
        url = "/same" if same_url else f"/page/{i}"
        entry = LogEntry(
            ip=ip,
            timestamp=ts,
            method="GET",
            url=url,
            status=200,
            size=1024,
            referer="https://example.com",
            user_agent=user_agent,
        )
        session.add_request(entry)
    
    return session


class TestLabelSession:
    """Tests for session labeling."""
    
    def test_known_bot_ua(self):
        """Known bot user agent should be labeled as bot."""
        session = _make_session(
            user_agent="python-requests/2.28.0",
            num_requests=5
        )
        label, confidence, _reason = label_session(session)
        assert label == 'bot'
        assert confidence >= 0.9
    
    def test_known_browser_ua(self):
        """Known browser user agent with normal behavior should be human."""
        session = _make_session(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            num_requests=10,
            timing="variable"
        )
        label, confidence, _reason = label_session(session)
        # Should lean human
        assert label == 'human' or confidence < 0.6
    
    def test_uniform_timing_bot(self):
        """Very uniform timing should be flagged as bot."""
        session = _make_session(
            user_agent="Mozilla/5.0",
            num_requests=10,
            timing="uniform"
        )
        label, confidence, _reason = label_session(session)
        # Uniform timing is a strong bot signal
        assert label == 'bot' or confidence > 0.5
    
    def test_same_url_bot(self):
        """All requests to same URL should be flagged."""
        session = _make_session(
            user_agent="Mozilla/5.0",
            num_requests=15,
            same_url=True
        )
        label, _confidence, _reason = label_session(session)
        assert label == 'bot'

    def test_empty_session(self):
        """Empty session should default to human."""
        session = Session("192.168.1.1", "Mozilla/5.0")
        label, _confidence, _reason = label_session(session)
        assert label == 'human'
    
    def test_unknown_ua_with_many_requests(self):
        """Unknown UA with many requests should lean bot."""
        session = _make_session(
            user_agent="SomeCustomTool/1.0",
            num_requests=20,
            timing="variable"
        )
        _label, confidence, _reason = label_session(session)
        # Unknown UA is a signal
        assert confidence >= 0.5


class TestLabelEntries:
    """Tests for batch labeling."""
    
    def test_label_entries(self):
        """Test labeling multiple entries."""
        entries = [
            LogEntry(
                ip="192.168.1.1",
                timestamp=datetime(2023, 3, 24, 17, 0, i, tzinfo=timezone.utc),
                method="GET",
                url=f"/page/{i}",
                status=200,
                size=1024,
                referer="https://example.com",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            )
            for i in range(20)
        ]
        
        results = label_entries(entries)
        assert len(results) >= 1
        for session, label, confidence, reason in results:
            assert label in ('bot', 'human')
            assert 0.0 <= confidence <= 1.0
            assert isinstance(reason, str)
