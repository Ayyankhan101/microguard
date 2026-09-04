"""Tests for the feature extraction module."""

from datetime import datetime, timedelta, timezone

from microguard.features import (
    FEATURE_NAMES,
    Session,
    _coefficient_of_variation,
    _shannon_entropy,
    _url_depth,
    _url_width,
    extract_features,
    group_into_sessions,
)
from microguard.parser import LogEntry


def _make_entry(
    ip: str = "192.168.1.1",
    timestamp: datetime | None = None,
    method: str = "GET",
    url: str = "/products",
    status: int = 200,
    size: int = 1234,
    referer: str = "https://example.com",
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
) -> LogEntry:
    """Create a test log entry."""
    if timestamp is None:
        timestamp = datetime(2023, 3, 24, 17, 7, 41, tzinfo=timezone.utc)
    return LogEntry(
        ip=ip,
        timestamp=timestamp,
        method=method,
        url=url,
        status=status,
        size=size,
        referer=referer,
        user_agent=user_agent,
    )


class TestHelpers:
    """Tests for helper functions."""
    
    def test_shannon_entropy_empty(self):
        assert _shannon_entropy("") == 0.0
    
    def test_shannon_entropy_uniform(self):
        # All same character = 0 entropy
        assert _shannon_entropy("aaaa") == 0.0
    
    def test_shannon_entropy_varied(self):
        # Mixed characters = higher entropy
        e1 = _shannon_entropy("abcd")
        e2 = _shannon_entropy("aaaa")
        assert e1 > e2
    
    def test_cv_empty(self):
        assert _coefficient_of_variation([]) == 0.0
    
    def test_cv_single(self):
        assert _coefficient_of_variation([1.0]) == 0.0
    
    def test_cv_uniform(self):
        # All same values = CV = 0
        assert _coefficient_of_variation([1.0, 1.0, 1.0]) == 0.0
    
    def test_cv_varied(self):
        # Varied values = higher CV
        cv = _coefficient_of_variation([1.0, 2.0, 3.0, 4.0, 5.0])
        assert cv > 0
    
    def test_url_depth(self):
        assert _url_depth("/") == 0
        assert _url_depth("/products") == 1
        assert _url_depth("/products/42") == 2
        assert _url_depth("/products/42/reviews") == 3
    
    def test_url_width(self):
        urls = ["/products", "/products/42", "/about"]
        assert _url_width(urls) == 2  # products, about


class TestFeatureExtraction:
    """Tests for feature extraction."""
    
    def test_empty_session(self):
        session = Session("192.168.1.1", "Mozilla/5.0")
        features = extract_features(session)
        assert len(features) == 19
        assert all(f == 0.0 for f in features)
    
    def test_single_request(self):
        session = Session("192.168.1.1", "Mozilla/5.0")
        session.add_request(_make_entry())
        features = extract_features(session)
        assert len(features) == 19
        # Session duration should be 0 (single request)
        assert features[4] == 0.0
    
    def test_multiple_requests(self):
        session = Session("192.168.1.1", "Mozilla/5.0")
        
        # Add 5 requests with 1-second gaps
        base_time = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            entry = _make_entry(
                timestamp=base_time + timedelta(seconds=i),
                url=f"/page/{i}",
            )
            session.add_request(entry)
        
        features = extract_features(session)
        assert len(features) == 19
        
        # Session duration should be ~4 seconds
        assert abs(features[4] - 4.0) < 0.1
        
        # Endpoint count should be 5
        assert features[5] == 5.0
    
    def test_bot_user_agent(self):
        session = Session("10.0.0.1", "python-requests/2.28.0")
        session.add_request(_make_entry(user_agent="python-requests/2.28.0"))
        
        features = extract_features(session)
        # ua_category should be 1 (bot)
        assert features[11] == 1.0
    
    def test_browser_user_agent(self):
        session = Session(
            "192.168.1.1",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        session.add_request(_make_entry(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        ))
        
        features = extract_features(session)
        # ua_category should be 0 (browser)
        assert features[11] == 0.0
        # has_accept_language should be 1 (browser)
        assert features[10] == 1.0
    
    def test_uniform_timing(self):
        """Bot-like uniform timing should have low CV."""
        session = Session("10.0.0.1", "python-requests/2.28.0")
        
        base_time = datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)
        for i in range(10):
            entry = _make_entry(
                timestamp=base_time + timedelta(seconds=i * 0.05),  # 50ms uniform
                url=f"/api/data?page={i}",
            )
            session.add_request(entry)
        
        features = extract_features(session)
        # CV should be very low (uniform timing)
        assert features[3] < 0.1
    
    def test_error_rate(self):
        """Sessions with many 4xx/5xx errors should have high error_rate."""
        session = Session("10.0.0.1", "python-requests/2.28.0")
        
        for i in range(10):
            status = 404 if i < 5 else 200  # 50% errors
            entry = _make_entry(status=status)
            session.add_request(entry)
        
        features = extract_features(session)
        # error_rate should be ~0.5
        assert abs(features[15] - 0.5) < 0.01
    
    def test_feature_count(self):
        """Verify we always extract exactly 19 features."""
        session = Session("192.168.1.1", "Mozilla/5.0")
        session.add_request(_make_entry())
        features = extract_features(session)
        assert len(features) == 19
        assert len(FEATURE_NAMES) == 19


class TestSessionGrouping:
    """Tests for session grouping."""
    
    def test_single_ip(self):
        entries = [
            _make_entry(ip="192.168.1.1", timestamp=datetime(2023, 3, 24, 17, 0, i, tzinfo=timezone.utc))
            for i in range(5)
        ]
        sessions = group_into_sessions(entries)
        assert len(sessions) == 1
        assert sessions[0].request_count == 5
    
    def test_multiple_ips(self):
        entries = [
            _make_entry(ip="192.168.1.1", timestamp=datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)),
            _make_entry(ip="192.168.1.2", timestamp=datetime(2023, 3, 24, 17, 0, 1, tzinfo=timezone.utc)),
        ]
        sessions = group_into_sessions(entries)
        assert len(sessions) == 2
    
    def test_timeout_splits_session(self):
        """Requests far apart should create new sessions."""
        entries = [
            _make_entry(ip="192.168.1.1", timestamp=datetime(2023, 3, 24, 17, 0, 0, tzinfo=timezone.utc)),
            _make_entry(ip="192.168.1.1", timestamp=datetime(2023, 3, 24, 18, 0, 0, tzinfo=timezone.utc)),  # 1 hour later
        ]
        sessions = group_into_sessions(entries, timeout_minutes=30)
        assert len(sessions) == 2
    
    def test_empty_entries(self):
        sessions = group_into_sessions([])
        assert len(sessions) == 0
