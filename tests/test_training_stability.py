"""Training must agree with inference, and must refuse to publish a collapse.

Three defects, found while retraining with columns neutralized:

1. A zero-range column normalizes to 0.5 during training and 0.0 at
   inference. `tests/test_training_pipeline.py` has pinned this divergence
   as known-unfixed; one column is affected in the shipped model
   (`method_mismatch_count`, constant 0.0 across the whole training set).
   Neutralizing ten columns turned the 0.007 average score shift into a
   model that labeled every session human.
2. `retrain_deployment_model` hands raw features to `train()` while
   `predict()` normalizes, so a deployment's fine-tuned model is fitted on
   one scale and served another. Same family as the missing-normalization
   bug in the CHANGELOG, on the path an operator actually drives.
3. Neither path checks that training produced anything. A constant
   classifier is saved and published like any other model.
"""

import contextlib
import json
import random

import pytest
from micrograd.nn import MLP

from microguard.model import BotDetector, DegenerateModelError
from microguard.training.online_update import (
    record_correction,
    retrain_deployment_model,
)
from microguard.training.train import normalize_features, train_model

FEATURE_COUNT = 19


def _separable_dataset(n: int = 120) -> tuple[list[list[float]], list[float]]:
    rng = random.Random(0)
    features, labels = [], []
    for i in range(n):
        bot = i % 2 == 0
        row = [rng.uniform(0.0, 1.0) for _ in range(FEATURE_COUNT)]
        row[3] = rng.uniform(0.6, 1.0) if bot else rng.uniform(0.0, 0.4)
        features.append(row)
        labels.append(1.0 if bot else 0.0)
    return features, labels


class TestConstantColumnsNormalizeTheSameWayInBothDirections:
    """The network must never be served an input it was not trained on."""

    def test_training_and_inference_agree_on_a_zero_range_column(self):
        mins = [3.0] * FEATURE_COUNT
        maxs = [3.0] * FEATURE_COUNT
        training_value = normalize_features([[3.0] * FEATURE_COUNT], mins, maxs)[0][0]

        detector = BotDetector()
        detector.norm_mins = mins
        detector.norm_maxs = maxs
        inference_value = detector.normalize([3.0] * FEATURE_COUNT)[0]

        assert inference_value == training_value

    def test_a_constant_column_no_longer_swallows_the_value(self):
        """0.0 and 99.0 in a zero-range column both mean 'no information'.

        They must still land on the value training used, not on zero.
        """
        detector = BotDetector()
        detector.norm_mins = [3.0] * FEATURE_COUNT
        detector.norm_maxs = [3.0] * FEATURE_COUNT

        assert detector.normalize([99.0] * FEATURE_COUNT)[0] == 0.5


class TestRetrainingFitsTheScaleItWillBeServed:
    """A model fitted on raw features and served normalized ones is noise."""

    def test_corrections_are_normalized_before_training(self, tmp_path, monkeypatch):
        baseline = BotDetector()
        baseline.norm_mins = [0.0] * FEATURE_COUNT
        baseline.norm_maxs = [100.0] * FEATURE_COUNT
        base_path = tmp_path / "model.json"
        baseline.save(str(base_path))
        (tmp_path / "normalization.json").write_text(
            json.dumps({"mins": baseline.norm_mins, "maxs": baseline.norm_maxs}),
            encoding="utf-8",
        )

        # Distinct features per class -- identical vectors with opposite
        # labels are unlearnable by construction and would trip the
        # degeneracy guard before this test got to look at anything.
        for i in range(60):
            bot = i % 2
            record_correction(
                deployment_id="d",
                decision_id=f"x{i}",
                features=[50.0 if bot else 20.0] * FEATURE_COUNT,
                confirmed_label=float(bot),
                feedback_dir=tmp_path,
            )

        seen: list[list[float]] = []
        original = BotDetector.train

        def spy(self, features, labels, **kwargs):
            seen.append(list(features[0]))
            return original(self, features, labels, **kwargs)

        monkeypatch.setattr(BotDetector, "train", spy)
        # Whether one epoch is enough to separate them is not what this test
        # is about; it asserts on the scale train() was handed.
        with contextlib.suppress(DegenerateModelError):
            retrain_deployment_model(
                "d", base_model_path=base_path, feedback_dir=tmp_path, epochs=1
            )

        # Raw values against bounds [0, 100] normalize to 0.5 and 0.2.
        assert set(seen[0]) <= {0.5, 0.2}, (
            f"train() was handed {seen[0][0]} -- a raw feature, not the "
            "normalized one predict() will serve"
        )


class TestCollapseIsRefusedNotPublished:
    """A constant classifier must raise, not write a plausible-looking file."""

    def test_a_model_that_predicts_one_class_is_rejected(self):
        features, labels = _separable_dataset()
        detector = BotDetector()
        for neuron in detector.model.layers[0].neurons:
            for weight in neuron.w:
                weight.data = 0.0
            neuron.b.data = -1.0

        with pytest.raises(DegenerateModelError):
            detector.check_not_degenerate(features, labels)

    def test_a_model_that_learned_the_pattern_passes(self):
        random.seed(42)
        features, labels = _separable_dataset()
        detector = BotDetector()
        detector.train(features, labels, epochs=25, learning_rate=0.05, verbose=False)

        detector.check_not_degenerate(features, labels)

    def test_a_retrain_that_collapses_publishes_nothing(self, tmp_path, monkeypatch):
        baseline = BotDetector()
        base_path = tmp_path / "model.json"
        baseline.save(str(base_path))

        for i in range(60):
            record_correction(
                deployment_id="d",
                decision_id=f"x{i}",
                features=[float(i % 2)] * FEATURE_COUNT,
                confirmed_label=float(i % 2),
                feedback_dir=tmp_path,
            )

        def collapse(self, features, labels, **kwargs):
            for neuron in self.model.layers[0].neurons:
                for weight in neuron.w:
                    weight.data = 0.0
                neuron.b.data = -1.0
            return []

        monkeypatch.setattr(BotDetector, "train", collapse)

        with pytest.raises(DegenerateModelError):
            retrain_deployment_model(
                "d", base_model_path=base_path, feedback_dir=tmp_path, epochs=1
            )
        assert not (tmp_path / "d_model.json").exists()

class TestRestartsRecoverACollapsedStart:
    """Redrawing beats grinding: more epochs do not revive a dead ReLU."""

    @pytest.mark.parametrize("seed", [4, 9, 22, 34])
    def test_a_seed_that_used_to_collapse_now_trains(self, seed):
        random.seed(seed)
        features, labels = _separable_dataset()
        detector = BotDetector()

        def redraw():
            detector.model = MLP(FEATURE_COUNT, [4, 1])

        detector.train_until_it_learns(
            features, labels, reset=redraw, epochs=10,
            learning_rate=0.05, verbose=False,
        )
        detector.check_not_degenerate(features, labels)

    def test_every_attempt_collapsing_still_raises(self):
        features, labels = _separable_dataset()
        detector = BotDetector()

        def kill(self, *args, **kwargs):
            for neuron in self.model.layers[0].neurons:
                for weight in neuron.w:
                    weight.data = 0.0
                neuron.b.data = -1.0
            return []

        BotDetector.train, original = kill, BotDetector.train
        try:
            with pytest.raises(DegenerateModelError, match="3 training attempts"):
                detector.train_until_it_learns(
                    features, labels, reset=lambda: None, attempts=3,
                )
        finally:
            BotDetector.train = original


class TestADeadBaselineIsNamedNotRetriedFiveTimes:
    """Fine-tuning reloads the baseline, so a dead baseline never recovers."""

    def test_a_baseline_that_cannot_fire_is_reported_as_such(self, tmp_path):
        baseline = BotDetector()
        for neuron in baseline.model.layers[0].neurons:
            for weight in neuron.w:
                weight.data = -1.0
            neuron.b.data = 0.0
        base_path = tmp_path / "model.json"
        baseline.save(str(base_path))

        for i in range(60):
            bot = i % 2
            record_correction(
                deployment_id="d",
                decision_id=f"x{i}",
                features=[0.5 if bot else 0.2] * FEATURE_COUNT,
                confirmed_label=float(bot),
                feedback_dir=tmp_path,
            )

        with pytest.raises(DegenerateModelError, match="baseline"):
            retrain_deployment_model(
                "d", base_model_path=base_path, feedback_dir=tmp_path, epochs=2
            )
        assert not (tmp_path / "d_model.json").exists()


class TestOfflineTrainingRefusesToPublishACollapse:
    """`train_model` writes data/model.json -- the file every install starts from."""

    def test_a_collapse_raises_instead_of_writing_a_model(self, tmp_path, monkeypatch):
        features, labels = _separable_dataset()

        def collapse(self, *args, **kwargs):
            for neuron in self.model.layers[0].neurons:
                for weight in neuron.w:
                    weight.data = 0.0
                neuron.b.data = -1.0
            return []

        monkeypatch.setattr(BotDetector, "train", collapse)
        model_path = tmp_path / "model.json"

        with pytest.raises(DegenerateModelError):
            train_model(features, labels, model_path=str(model_path), epochs=1)

        assert not model_path.exists()

    def test_a_collapsed_first_attempt_is_retried(self, tmp_path, monkeypatch):
        features, labels = _separable_dataset()
        original = BotDetector.train
        calls = {"n": 0}

        def collapse_once(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                for neuron in self.model.layers[0].neurons:
                    for weight in neuron.w:
                        weight.data = 0.0
                    neuron.b.data = -1.0
                return []
            return original(self, *args, **kwargs)

        monkeypatch.setattr(BotDetector, "train", collapse_once)
        model_path = tmp_path / "model.json"
        random.seed(42)

        train_model(features, labels, model_path=str(model_path), epochs=25)

        assert calls["n"] > 1, "the collapsed attempt was not retried"
        assert model_path.exists()
