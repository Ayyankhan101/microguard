"""Ground-truth attack-pattern matcher for the organization-x dataset.

`data/zenodo_data/organization-x/ground-truth/organization-x.yaml` defines
real forensic labeling rules used to tag known-malicious requests in a real
Apache access log: each rule is a set of case-insensitive substrings that
must ALL appear in a log line for that line to count as an instance of the
named attack category (sql_injection_attempt, rce_shell, dir_scan, etc.).

Hand-parsed rather than via a YAML library: the file's structure is a flat,
fixed shape (a list of `id`/`ground_truth_label`/`sensitivity`/`filter`
blocks), and avoiding a new dependency keeps this project's "zero external
dependencies beyond micrograd" property intact.
"""

from dataclasses import dataclass

from ..features import Session
from ..parser import LogEntry


@dataclass(frozen=True)
class GroundTruthRule:
    """One labeling rule: line matches if ALL of `filter_tokens` are present."""
    id: str
    ground_truth_label: str
    sensitivity: str
    filter_tokens: tuple[str, ...]


def _normalize_filter_token(token: str) -> str:
    """Lowercase a filter token and strip SQL-LIKE-style `%` wildcard markers.

    The yaml mixes literal substrings (`"etc/passwd"`) with `%`-bracketed
    tokens (`"%POST%"`, `"%400%"`) — treating a leading/trailing `%` as a
    stripped wildcard marker (rather than a literal character to search
    for) is the simplest reading that makes every rule in the file useful;
    literal `%` essentially never brackets a whole real-world token.
    """
    return token.strip().strip('%').lower()


def load_rules(yaml_path: str) -> list[GroundTruthRule]:
    """Parse organization-x.yaml's flat rule-block structure."""
    rules: list[GroundTruthRule] = []
    current_id = None
    current_label = None
    current_sensitivity = None
    current_filters: list[str] = []

    def _flush():
        if current_id is not None and current_filters:
            rules.append(GroundTruthRule(
                id=current_id,
                ground_truth_label=current_label or current_id,
                sensitivity=current_sensitivity or 'moderate',
                filter_tokens=tuple(_normalize_filter_token(t) for t in current_filters),
            ))

    with open(yaml_path, encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.rstrip('\n')
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            if stripped.startswith('- id:'):
                _flush()
                current_id = stripped[len('- id:'):].strip()
                current_label = None
                current_sensitivity = None
                current_filters = []
            elif stripped.startswith('ground_truth_label:'):
                current_label = stripped[len('ground_truth_label:'):].strip()
            elif stripped.startswith('sensitivity:'):
                current_sensitivity = stripped[len('sensitivity:'):].strip()
            elif stripped.startswith('filter:'):
                continue
            elif stripped.startswith('- '):
                # A filter-list item: `- "token"` (quotes optional).
                value = stripped[2:].strip()
                if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                    value = value[1:-1]
                current_filters.append(value)

    _flush()
    return rules


def _build_searchable_text(entry: LogEntry) -> str:
    """Lowercased text to match filter tokens against.

    Includes a synthesized `code: {status}` fragment alongside the raw log
    line, since a handful of rules are written in a log2timeline/plaso-style
    vocabulary (`"code: 404"`) rather than the raw Apache combined format.
    This is a pragmatic stand-in for actually running log2timeline (which
    the yaml's header comment calls for) — not a claim of full fidelity.
    """
    return f"{entry.raw_line} code: {entry.status}".lower()


def match_line(entry: LogEntry, rules: list[GroundTruthRule]) -> str | None:
    """Return the first matching rule's ground_truth_label, or None."""
    text = _build_searchable_text(entry)
    for rule in rules:
        if all(token in text for token in rule.filter_tokens):
            return rule.ground_truth_label
    return None


def label_line(entry: LogEntry, rules: list[GroundTruthRule]) -> tuple[str, str | None]:
    """Label one log line.

    Returns ('bot', category) if a rule matched, else ('unlabeled', None) —
    deliberately not 'human': the absence of a known attack signature isn't
    evidence of humanity, just the absence of a *known* one (e.g. generic
    monitoring-bot traffic has no organization-x ground-truth rule but is
    still clearly automated).
    """
    category = match_line(entry, rules)
    if category is not None:
        return 'bot', category
    return 'unlabeled', None


def label_sessions_by_groundtruth(
    sessions: list[Session],
    rules: list[GroundTruthRule],
) -> list[tuple[Session, str, str | None]]:
    """Label sessions from ground-truth rule matches.

    A session is 'bot' (source of truth: ground truth) if ANY of its
    requests matches an attack rule — a single confirmed malicious request
    is sufficient evidence for the session, and a percentage-based
    threshold risks diluting a real attacker's session with intermixed
    recon requests into a false 'human' label. Sessions with zero matches
    are returned as 'unlabeled' (category None) for the caller to route
    through the heuristic labeler instead of assuming 'human'.

    Returns:
        List of (session, label, category) tuples, where category is the
        most frequent matched ground_truth_label in the session (or None).
    """
    from collections import Counter

    results: list[tuple[Session, str, str | None]] = []
    for session in sessions:
        categories = []
        for entry in session.requests:
            category = match_line(entry, rules)
            if category is not None:
                categories.append(category)

        if categories:
            top_category = Counter(categories).most_common(1)[0][0]
            results.append((session, 'bot', top_category))
        else:
            results.append((session, 'unlabeled', None))

    return results
