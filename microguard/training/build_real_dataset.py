"""Build a bot-training dataset from real, ground-truth-labeled traffic.

Replaces a 100%-synthetic bot class with real attack traffic already
present in this repo (`data/zenodo_data/organization-x/`) — real Apache
logs from a production server, with `organization-x.yaml` providing
genuine forensic ground-truth labels (sql injection, RCE, directory
scanning, brute-force, etc.), rather than `random.uniform()` guesses at
what a bot "should" look like.

Important dataset quirk, discovered by inspection (not assumed): this
dataset's client IP is effectively anonymized to ~2 placeholder values
across all 213K+ lines, so the normal IP-based sessionizer collapses
everything into a handful of sessions. Sessions here are grouped by
`(ip, user_agent)` instead (see `features.group_into_sessions`'s
`session_key` parameter) — a best-effort proxy for "one actor," not a
perfect one. Because of that same anonymization, this dataset is used as
**bot-side evidence only**: a common desktop-browser UA string plausibly
represents many distinct real humans merged into one training "session,"
which would bias human-side features if trusted. The human class continues
to come from `data/harvard_training_data.json`'s genuinely distinct real
sessions, unchanged.
"""

import glob
import json
import os
import random

from ..features import Session, extract_features, group_into_sessions
from ..labeler import label_session
from ..parser import LogEntry, parse_file
from . import generate
from .groundtruth import label_sessions_by_groundtruth, load_rules

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'data')
ORGX_DIR = os.path.join(DATA_DIR, 'zenodo_data', 'organization-x')
ORGX_RULES_PATH = os.path.join(ORGX_DIR, 'ground-truth', 'organization-x.yaml')
ORGX_LOG_GLOB = os.path.join(ORGX_DIR, 'log', 'apache2', 'web-access.log*')

# Synthetic bot sessions are capped at this fraction of the final bot class
# — real, ground-truth/heuristic-labeled sessions are the majority. This
# exists because several real attack categories (rce_java, api_call,
# rce_sysinfo, ...) collapse to a handful of (ip, user_agent) groups once
# grouped, too few on their own to teach an 85-parameter MLP anything.
MAX_SYNTHETIC_FRACTION = 0.3


def load_all_organization_x_entries() -> list[LogEntry]:
    """Parse every organization-x web-access.log rotation (plain + .gz)."""
    entries: list[LogEntry] = []
    for path in sorted(glob.glob(ORGX_LOG_GLOB)):
        entries.extend(parse_file(path))
    return entries


def group_into_actor_sessions(entries: list[LogEntry], timeout_minutes: int = 30) -> list[Session]:
    """Group by (ip, user_agent) — see module docstring for why."""
    return group_into_sessions(
        entries, timeout_minutes, session_key=lambda e: (e.ip, e.user_agent)
    )


def _session_group_id(session: Session) -> str:
    return f"{session.ip}|{session.user_agent}"


def build_dataset(seed: int = 42) -> dict:
    """Build the combined real (+ capped synthetic) training dataset."""
    random.seed(seed)

    rules = load_rules(ORGX_RULES_PATH)
    entries = load_all_organization_x_entries()
    sessions = group_into_actor_sessions(entries)
    gt_results = label_sessions_by_groundtruth(sessions, rules)

    features: list[list[float]] = []
    labels: list[float] = []
    group_ids: list[str] = []
    provenance: list[str] = []
    category_counts: dict[str, int] = {}

    real_bot_count = 0
    for session, gt_label, category in gt_results:
        if session.request_count < 3:
            continue

        if gt_label == 'bot':
            assert category is not None  # label_sessions_by_groundtruth guarantees this for 'bot'
            features.append(extract_features(session))
            labels.append(1.0)
            group_ids.append(_session_group_id(session))
            provenance.append('ground_truth')
            category_counts[category] = category_counts.get(category, 0) + 1
            real_bot_count += 1
        else:
            # No ground-truth match — fall back to the heuristic labeler,
            # but only keep it if the heuristic also says 'bot'. This
            # dataset is bot-side evidence only (see module docstring);
            # sessions the heuristic calls human/automated-integration are
            # dropped rather than trusted as human examples.
            heuristic_label, _confidence, _reason = label_session(session)
            if heuristic_label == 'bot':
                features.append(extract_features(session))
                labels.append(1.0)
                group_ids.append(_session_group_id(session))
                provenance.append('heuristic_real')
                real_bot_count += 1

    # Human class: Harvard's real, genuinely-distinct sessions, unchanged.
    harvard_path = os.path.join(DATA_DIR, 'harvard_training_data.json')
    human_count = 0
    if os.path.exists(harvard_path):
        with open(harvard_path) as f:
            harvard = json.load(f)
        for i, (feat, label) in enumerate(zip(harvard['features'], harvard['labels'])):
            if label <= 0.5:
                features.append(feat)
                labels.append(0.0)
                group_ids.append(f'harvard_human_{i}')
                provenance.append('harvard_human')
                human_count += 1

    # Synthetic top-up: only enough to keep the bot class from being
    # tiny relative to the human class, capped so real data remains the
    # majority of the bot class.
    target_bot_total = max(real_bot_count, int(human_count * 0.5))
    max_synthetic = int(target_bot_total * MAX_SYNTHETIC_FRACTION)
    synthetic_needed = min(max_synthetic, max(0, target_bot_total - real_bot_count))

    for i in range(synthetic_needed):
        features.append(generate.generate_bot_session())
        labels.append(1.0)
        group_ids.append(f'synthetic_bot_{i}')
        provenance.append('synthetic_augmentation')

    # Shuffle (features/labels/group_ids/provenance stay aligned)
    combined = list(zip(features, labels, group_ids, provenance))
    random.shuffle(combined)
    features, labels, group_ids, provenance = (list(x) for x in zip(*combined))

    n_bot = sum(1 for l in labels if l > 0.5)
    n_human = len(labels) - n_bot
    source_counts: dict[str, int] = {}
    for p in provenance:
        source_counts[p] = source_counts.get(p, 0) + 1

    return {
        'features': features,
        'labels': labels,
        'group_ids': group_ids,
        'provenance': provenance,
        'n_samples': len(features),
        'n_features': len(features[0]) if features else 0,
        'n_human': n_human,
        'n_bot': n_bot,
        'source_counts': source_counts,
        'category_counts': category_counts,
        'sources': ['organization_x_ground_truth', 'organization_x_heuristic',
                    'harvard_human', 'synthetic_augmentation'],
    }


def main():
    dataset = build_dataset()
    out_path = os.path.join(DATA_DIR, 'real_bot_training_data.json')
    with open(out_path, 'w') as f:
        json.dump(dataset, f, indent=2)

    print(f"💾 Dataset saved to: {out_path}")
    print(f"   Samples: {dataset['n_samples']} (Human: {dataset['n_human']}, Bot: {dataset['n_bot']})")
    print(f"   Source breakdown: {dataset['source_counts']}")
    print(f"   Ground-truth attack categories: {dataset['category_counts']}")


if __name__ == '__main__':
    main()
