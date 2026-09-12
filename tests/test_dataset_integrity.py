"""The training set must not hand the model the answer.

The shipped model is a two-value step function: 413 of 623 held-out sessions
score exactly 0.731, which is `sigmoid(output bias)` with every hidden unit
off. It reads one column. `header_consistency_score` is 0.7 for every human
and 1.0 for every bot, and `features.py`'s `1.0 / len(ua_variants)` can only
ever produce 1.0, 0.5, 0.333... -- 0.7 is not reachable, so that value came
from a data generator rather than from the extractor. The 100% held-out
accuracy in `test_training_quality.py` is measuring the leak.

The cause is structural, not one bad column. The human class comes from a
single file (`harvard_training_data.json`), so ANY column constant within it
is a source fingerprint, and source is 1:1 with label. Ten columns qualify.

These two tests are the guard that makes that unshippable, whatever dataset is
used later. Both are `xfail(strict=True)` rather than skipped or deleted: they
describe the state the dataset must reach, and `strict` means CI reports it the
day real data makes them pass. See docs/explanation-training-data.md.
"""

import json
import os

import pytest

from microguard.features import FEATURE_NAMES

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')


def _load_training_data():
    """Whichever file `training/train.py` actually trains on.

    Mirrors that module's priority order so this tracks reality rather than
    assuming a filename, the same way `test_training_quality.py` does.
    """
    for name in ('real_bot_training_data.json', 'harvard_training_data.json'):
        path = os.path.join(DATA_DIR, name)
        if os.path.exists(path):
            with open(path, encoding='utf-8') as handle:
                return json.load(handle), name
    pytest.skip("no training data found")


def _columns_by_class(data):
    """(per-column human values, per-column bot values)."""
    labels = [float(lbl) for lbl in data['labels']]
    columns = list(zip(*data['features']))
    human, bot = [], []
    for column in columns:
        human.append([v for v, lbl in zip(column, labels) if lbl <= 0.5])
        bot.append([v for v, lbl in zip(column, labels) if lbl > 0.5])
    return human, bot


class TestNoColumnIsASourceFingerprint:
    """A column constant within one class identifies the file, not the class."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "8 columns are a single constant across the whole human class, "
            "because the human class comes from exactly one generated file: "
            "endpoint_sequence_entropy, has_accept_language, ua_category, "
            "payload_entropy, status_code_entropy, error_rate, image_ratio, "
            "night_ratio. (method_mismatch_count is constant in BOTH classes "
            "-- useless, not a leak -- and header_consistency_score is caught "
            "by the separability test below.) Fixing this needs real human "
            "sessions extracted by the same extract_features as the bot "
            "class; see docs/explanation-training-data.md."
        ),
    )
    def test_no_column_is_constant_within_a_class(self):
        data, source = _load_training_data()
        human, bot = _columns_by_class(data)

        offenders = []
        for i, name in enumerate(FEATURE_NAMES):
            human_constant = len(set(human[i])) == 1
            bot_constant = len(set(bot[i])) == 1
            # A column constant in BOTH classes at the same value carries no
            # information at all, which is useless but not a leak. A column
            # constant in one class and varying in the other is a fingerprint.
            if human_constant != bot_constant:
                side = "human" if human_constant else "bot"
                value = human[i][0] if human_constant else bot[i][0]
                offenders.append(f"{name} (constant {value} across {side})")

        assert not offenders, (
            f"{len(offenders)} fingerprint column(s) in {source}:\n  "
            + "\n  ".join(offenders)
        )


class TestNoColumnPerfectlySeparatesTheClasses:
    """Zero overlap in a real behavioural feature means it is not behavioural."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "header_consistency_score is 0.7 for all 1000 humans and 1.0 for "
            "all 2580 bots -- zero overlap, and 0.7 is unreachable from "
            "features.py's 1.0/len(ua_variants). It is the column the shipped "
            "model learned."
        ),
    )
    def test_no_column_separates_the_classes_without_overlap(self):
        data, source = _load_training_data()
        human, bot = _columns_by_class(data)

        offenders = []
        for i, name in enumerate(FEATURE_NAMES):
            if not human[i] or not bot[i]:
                continue
            if max(human[i]) < min(bot[i]) or max(bot[i]) < min(human[i]):
                offenders.append(
                    f"{name} (human {min(human[i])}..{max(human[i])}, "
                    f"bot {min(bot[i])}..{max(bot[i])})"
                )

        assert not offenders, (
            f"{len(offenders)} perfectly separable column(s) in {source} -- "
            f"each one is the label in disguise:\n  " + "\n  ".join(offenders)
        )


class TestTheHumanClassHasMoreThanOneSource:
    """The structural cause, asserted directly rather than via its symptoms.

    Every fingerprint column above follows from this one fact. A dataset whose
    human class comes from one generator can always be separated by detecting
    that generator, however many individual columns get patched.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "every human row is provenance 'harvard_human'. Real human "
            "sessions require observe-only collection from live traffic; "
            "there is no second source in the repo."
        ),
    )
    def test_human_rows_come_from_more_than_one_provenance(self):
        data, source = _load_training_data()
        provenance = data.get('provenance')
        if provenance is None:
            pytest.skip(f"{source} carries no provenance breakdown")

        labels = [float(lbl) for lbl in data['labels']]
        human_sources = {
            prov for prov, lbl in zip(provenance, labels) if lbl <= 0.5
        }

        assert len(human_sources) > 1, (
            f"the entire human class in {source} comes from {human_sources} "
            "-- any column constant in that source is a label detector"
        )
