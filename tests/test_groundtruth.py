"""Tests for microguard.training.groundtruth."""

import os
from datetime import datetime, timezone

from microguard.parser import LogEntry
from microguard.training.groundtruth import (
    GroundTruthRule,
    label_line,
    label_sessions_by_groundtruth,
    load_rules,
    match_line,
)

ORGX_YAML = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'data', 'zenodo_data', 'organization-x', 'ground-truth', 'organization-x.yaml',
)


def _entry(url='/', status=200, ua='Mozilla/5.0', ip='1.2.3.4', raw_line=None):
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    line = raw_line or f'{ip} - - [01/Jan/2024:00:00:00 +0000] "GET {url} HTTP/1.1" {status} 100 "-" "{ua}"'
    return LogEntry(ip=ip, timestamp=ts, method='GET', url=url, status=status,
                     size=100, referer='-', user_agent=ua, raw_line=line)


class TestMatchLine:
    def test_all_tokens_must_match(self):
        # GroundTruthRule stores already-normalized tokens (normalization
        # happens in load_rules); constructing one directly here means
        # passing tokens as load_rules would have produced them.
        rule = GroundTruthRule(id='x', ground_truth_label='x', sensitivity='moderate',
                                filter_tokens=('select', 'union'))
        matching = _entry(raw_line='GET /?q=select+1+union+2 HTTP/1.1 200')
        assert match_line(matching, [rule]) == 'x'

    def test_partial_match_fails(self):
        rule = GroundTruthRule(id='x', ground_truth_label='x', sensitivity='moderate',
                                filter_tokens=('select', 'union'))
        partial = _entry(raw_line='GET /?q=select+1 HTTP/1.1 200')
        assert match_line(partial, [rule]) is None

    def test_case_insensitive(self):
        rule = GroundTruthRule(id='x', ground_truth_label='x', sensitivity='moderate',
                                filter_tokens=('etc/passwd',))
        entry = _entry(raw_line='GET /read?file=ETC/PASSWD HTTP/1.1 200')
        assert match_line(entry, [rule]) == 'x'

    def test_status_code_synthesized_fragment(self):
        rule = GroundTruthRule(id='dir_scan', ground_truth_label='dir_scan', sensitivity='moderate',
                                filter_tokens=('code: 404', 'trident/4.0'))
        entry = _entry(status=404, ua='Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 5.1; Trident/4.0)')
        assert match_line(entry, [rule]) == 'dir_scan'

    def test_first_rule_wins(self):
        rules = [
            GroundTruthRule(id='a', ground_truth_label='a', sensitivity='moderate', filter_tokens=('foo',)),
            GroundTruthRule(id='b', ground_truth_label='b', sensitivity='moderate', filter_tokens=('foo',)),
        ]
        entry = _entry(raw_line='GET /foo HTTP/1.1 200')
        assert match_line(entry, rules) == 'a'

    def test_no_rules_no_match(self):
        assert match_line(_entry(), []) is None


class TestLabelLine:
    def test_matched_is_bot(self):
        rule = GroundTruthRule(id='x', ground_truth_label='sqli', sensitivity='moderate',
                                filter_tokens=('select',))
        label, category = label_line(_entry(raw_line='GET /?q=select HTTP/1.1 200'), [rule])
        assert label == 'bot'
        assert category == 'sqli'

    def test_unmatched_is_unlabeled_not_human(self):
        label, category = label_line(_entry(), [])
        assert label == 'unlabeled'
        assert category is None


class TestLoadRulesRealFile:
    """Integration test against the actual organization-x.yaml shipped in data/."""

    def test_file_exists(self):
        assert os.path.exists(ORGX_YAML), "organization-x.yaml not found — dataset missing?"

    def test_loads_expected_rule_count(self):
        rules = load_rules(ORGX_YAML)
        assert len(rules) == 39

    def test_known_category_present(self):
        rules = load_rules(ORGX_YAML)
        labels = {r.ground_truth_label for r in rules}
        assert 'sql_injection_attempt' in labels
        assert 'dir_scan' in labels
        assert 'rce_shell' in labels

    def test_known_line_matches_dir_scan_go(self):
        rules = load_rules(ORGX_YAML)
        entry = _entry(raw_line='1.2.3.4 - - [01/Jan/2024:00:00:00 +0000] "GET /x HTTP/1.1" 200 100 "-" "Go-http-client/1.1"')
        assert match_line(entry, rules) == 'dir_scan_go'

    def test_ordinary_browser_request_no_match(self):
        rules = load_rules(ORGX_YAML)
        entry = _entry(
            raw_line='1.2.3.4 - - [01/Jan/2024:00:00:00 +0000] "GET /home HTTP/1.1" 200 100 '
                     '"https://example.com/" "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"'
        )
        assert match_line(entry, rules) is None


class _FakeSession:
    def __init__(self, requests):
        self.requests = requests


class TestLabelSessionsByGroundtruth:
    def test_any_match_labels_whole_session_bot(self):
        rule = GroundTruthRule(id='x', ground_truth_label='sqli', sensitivity='moderate',
                                filter_tokens=('select',))
        clean = _entry(raw_line='GET /home HTTP/1.1 200')
        malicious = _entry(raw_line='GET /?q=select HTTP/1.1 200')
        session = _FakeSession([clean, malicious, clean])

        results = label_sessions_by_groundtruth([session], [rule])
        assert len(results) == 1
        _sess, label, category = results[0]
        assert label == 'bot'
        assert category == 'sqli'

    def test_no_match_is_unlabeled(self):
        rule = GroundTruthRule(id='x', ground_truth_label='sqli', sensitivity='moderate',
                                filter_tokens=('select',))
        session = _FakeSession([_entry(), _entry()])

        results = label_sessions_by_groundtruth([session], [rule])
        _sess, label, category = results[0]
        assert label == 'unlabeled'
        assert category is None

    def test_most_frequent_category_wins(self):
        rule_a = GroundTruthRule(id='a', ground_truth_label='a', sensitivity='moderate', filter_tokens=('aaa',))
        rule_b = GroundTruthRule(id='b', ground_truth_label='b', sensitivity='moderate', filter_tokens=('bbb',))
        session = _FakeSession([
            _entry(raw_line='GET /aaa HTTP/1.1 200'),
            _entry(raw_line='GET /aaa HTTP/1.1 200'),
            _entry(raw_line='GET /bbb HTTP/1.1 200'),
        ])
        results = label_sessions_by_groundtruth([session], [rule_a, rule_b])
        _sess, label, category = results[0]
        assert label == 'bot'
        assert category == 'a'
