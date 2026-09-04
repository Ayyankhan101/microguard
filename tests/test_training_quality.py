"""Tests for training data quality, normalization, and model accuracy.

Validates:
- Training data format (features length, label values, no NaN/inf)
- Normalization params (mins/maxs consistency, zero-range handling)
- Model accuracy thresholds (>=95% on Harvard data, per-class accuracy)
"""

import json
import math
import os
import pytest

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_harvard():
    path = os.path.join(DATA_DIR, 'harvard_training_data.json')
    if not os.path.exists(path):
        pytest.skip("Harvard training data not found")
    with open(path) as f:
        return json.load(f)


def _load_normalization():
    path = os.path.join(DATA_DIR, 'normalization.json')
    if not os.path.exists(path):
        pytest.skip("normalization.json not found")
    with open(path) as f:
        return json.load(f)


def _load_model():
    from microguard.model import BotDetector
    path = os.path.join(DATA_DIR, 'model.json')
    if not os.path.exists(path):
        pytest.skip("model.json not found")
    return BotDetector(path)


# ---------------------------------------------------------------------------
# Training Data Format Tests
# ---------------------------------------------------------------------------

class TestHarvardDataFormat:
    """Validate the Harvard training data JSON structure."""

    def test_file_exists(self):
        path = os.path.join(DATA_DIR, 'harvard_training_data.json')
        assert os.path.exists(path), "harvard_training_data.json not found in data/"

    def test_required_keys(self):
        data = _load_harvard()
        required = {'features', 'labels', 'n_samples', 'n_features', 'n_human', 'n_bot'}
        assert required.issubset(data.keys()), f"Missing keys: {required - data.keys()}"

    def test_feature_count_matches_labels(self):
        data = _load_harvard()
        assert len(data['features']) == len(data['labels'])

    def test_sample_count_matches_metadata(self):
        data = _load_harvard()
        assert len(data['features']) == data['n_samples']

    def test_human_bot_counts(self):
        data = _load_harvard()
        actual_bot = sum(1 for l in data['labels'] if l > 0.5)
        actual_human = len(data['labels']) - actual_bot
        assert actual_bot == data['n_bot']
        assert actual_human == data['n_human']

    def test_balanced_classes(self):
        data = _load_harvard()
        assert data['n_human'] == data['n_bot'], "Classes should be balanced"

    def test_feature_vector_length(self):
        data = _load_harvard()
        expected = data['n_features']
        for i, feat in enumerate(data['features']):
            assert len(feat) == expected, (
                f"Sample {i}: expected {expected} features, got {len(feat)}"
            )

    def test_all_features_are_numbers(self):
        data = _load_harvard()
        for i, feat in enumerate(data['features']):
            for j, v in enumerate(feat):
                assert isinstance(v, (int, float)), (
                    f"Sample {i}, feature {j}: expected number, got {type(v).__name__}"
                )

    def test_no_nan_features(self):
        data = _load_harvard()
        for i, feat in enumerate(data['features']):
            for j, v in enumerate(feat):
                assert not math.isnan(v), f"Sample {i}, feature {j}: NaN"

    def test_no_inf_features(self):
        data = _load_harvard()
        for i, feat in enumerate(data['features']):
            for j, v in enumerate(feat):
                assert not math.isinf(v), f"Sample {i}, feature {j}: Inf"

    def test_labels_are_binary(self):
        data = _load_harvard()
        unique = set(data['labels'])
        assert unique <= {0.0, 1.0}, f"Unexpected label values: {unique - {0.0, 1.0}}"

    def test_positive_features(self):
        """All features should be non-negative (they're counts, ratios, times)."""
        data = _load_harvard()
        for i, feat in enumerate(data['features']):
            for j, v in enumerate(feat):
                assert v >= 0.0, f"Sample {i}, feature {j}: negative value {v}"


# ---------------------------------------------------------------------------
# Normalization Tests
# ---------------------------------------------------------------------------

class TestNormalization:
    """Validate normalization params are consistent with training data."""

    def test_file_exists(self):
        path = os.path.join(DATA_DIR, 'normalization.json')
        assert os.path.exists(path), "normalization.json not found in data/"

    def test_has_mins_maxs(self):
        norm = _load_normalization()
        assert 'mins' in norm
        assert 'maxs' in norm

    def test_mins_maxs_same_length(self):
        norm = _load_normalization()
        assert len(norm['mins']) == len(norm['maxs'])

    def test_feature_count(self):
        norm = _load_normalization()
        assert len(norm['mins']) == 19, f"Expected 19 features, got {len(norm['mins'])}"

    def test_maxs_greater_than_mins(self):
        """For features with range, max must be > min."""
        norm = _load_normalization()
        for i, (mn, mx) in enumerate(zip(norm['mins'], norm['maxs'])):
            assert mx >= mn, f"Feature {i}: max ({mx}) < min ({mn})"

    def test_zero_range_features(self):
        """Features 8, 13, 16 should have zero range (always 0 in Harvard data)."""
        norm = _load_normalization()
        zero_range = [8, 13, 16]
        for idx in zero_range:
            assert norm['mins'][idx] == norm['maxs'][idx], (
                f"Feature {idx}: expected zero range, got min={norm['mins'][idx]} max={norm['maxs'][idx]}"
            )

    def test_consistent_with_training_data(self):
        """Normalization mins/maxs should match training data ranges."""
        data = _load_harvard()
        norm = _load_normalization()
        features = data['features']

        for j in range(len(norm['mins'])):
            actual_min = min(f[j] for f in features)
            actual_max = max(f[j] for f in features)
            assert norm['mins'][j] == pytest.approx(actual_min, abs=1e-10), (
                f"Feature {j}: norm min {norm['mins'][j]} != data min {actual_min}"
            )
            assert norm['maxs'][j] == pytest.approx(actual_max, abs=1e-10), (
                f"Feature {j}: norm max {norm['maxs'][j]} != data max {actual_max}"
            )

    def test_no_nan_in_params(self):
        norm = _load_normalization()
        for i, v in enumerate(norm['mins']):
            assert not math.isnan(v), f"mins[{i}] is NaN"
        for i, v in enumerate(norm['maxs']):
            assert not math.isnan(v), f"maxs[{i}] is NaN"

    def test_no_inf_in_params(self):
        norm = _load_normalization()
        for i, v in enumerate(norm['mins']):
            assert not math.isinf(v), f"mins[{i}] is Inf"
        for i, v in enumerate(norm['maxs']):
            assert not math.isinf(v), f"maxs[{i}] is Inf"


# ---------------------------------------------------------------------------
# Model Accuracy Tests
# ---------------------------------------------------------------------------

class TestModelAccuracy:
    """Validate trained model meets accuracy thresholds on Harvard data."""

    def test_model_exists(self):
        path = os.path.join(DATA_DIR, 'model.json')
        assert os.path.exists(path), "model.json not found in data/"

    def test_model_loads(self):
        model = _load_model()
        assert model.NUM_FEATURES == 19

    def test_normalization_loaded(self):
        model = _load_model()
        assert model.norm_mins is not None, "Model should have normalization params"
        assert model.norm_maxs is not None
        assert len(model.norm_mins) == 19
        assert len(model.norm_maxs) == 19

    def test_overall_accuracy_threshold(self):
        """Model should achieve >=95% accuracy on Harvard training data."""
        model = _load_model()
        data = _load_harvard()

        correct = 0
        total = len(data['features'])
        for feat, label in zip(data['features'], data['labels']):
            pred = model.predict(feat)
            if (pred > 0.5) == (label > 0.5):
                correct += 1

        accuracy = correct / total
        assert accuracy >= 0.95, f"Accuracy {accuracy:.1%} below 95% threshold"

    def test_bot_detection_rate(self):
        """Model should detect >=90% of bots (recall)."""
        model = _load_model()
        data = _load_harvard()

        bot_samples = [(f, l) for f, l in zip(data['features'], data['labels']) if l > 0.5]
        detected = sum(1 for f, l in bot_samples if model.predict(f) > 0.5)
        recall = detected / len(bot_samples)

        assert recall >= 0.90, f"Bot recall {recall:.1%} below 90% threshold"

    def test_human_pass_rate(self):
        """Model should pass >=90% of humans (true negative rate)."""
        model = _load_model()
        data = _load_harvard()

        human_samples = [(f, l) for f, l in zip(data['features'], data['labels']) if l <= 0.5]
        passed = sum(1 for f, l in human_samples if model.predict(f) <= 0.5)
        tnr = passed / len(human_samples)

        assert tnr >= 0.90, f"Human pass rate {tnr:.1%} below 90% threshold"

    def test_score_range(self):
        """All predictions should be between 0 and 1."""
        model = _load_model()
        data = _load_harvard()

        for feat in data['features']:
            score = model.predict(feat)
            assert 0.0 <= score <= 1.0, f"Score {score} out of range"

    def test_bot_scores_higher_than_human(self):
        """Median bot score should be higher than median human score."""
        model = _load_model()
        data = _load_harvard()

        bot_scores = sorted(model.predict(f) for f, l in zip(data['features'], data['labels']) if l > 0.5)
        human_scores = sorted(model.predict(f) for f, l in zip(data['features'], data['labels']) if l <= 0.5)

        median_bot = bot_scores[len(bot_scores) // 2]
        median_human = human_scores[len(human_scores) // 2]

        assert median_bot > median_human, (
            f"Median bot score ({median_bot:.3f}) should be > median human score ({median_human:.3f})"
        )

    def test_no_extreme_scores(self):
        """No score should be exactly 0.0 or 1.0 (would indicate overconfidence)."""
        model = _load_model()
        data = _load_harvard()

        for feat in data['features']:
            score = model.predict(feat)
            assert score != 0.0, f"Score is exactly 0.0 (overconfident)"
            assert score != 1.0, f"Score is exactly 1.0 (overconfident)"


# ---------------------------------------------------------------------------
# Cross-Dataset Consistency Tests
# ---------------------------------------------------------------------------

class TestCrossDataset:
    """Validate model works consistently across different data sources."""

    def test_real_training_data_format(self):
        """real_training_data.json should also have correct format."""
        path = os.path.join(DATA_DIR, 'real_training_data.json')
        if not os.path.exists(path):
            pytest.skip("real_training_data.json not found")

        with open(path) as f:
            data = json.load(f)

        assert 'features' in data
        assert 'labels' in data
        assert len(data['features']) == len(data['labels'])

        for feat in data['features']:
            assert len(feat) == 19

    def test_model_handles_zero_range_features(self):
        """Features 8, 13, 16 (zero range) should produce valid scores."""
        model = _load_model()
        norm = _load_normalization()

        # Create a feature vector with zero-range features set to 0
        feat = [0.5] * 19
        feat[8] = 0.0
        feat[13] = 0.0
        feat[16] = 0.0

        score = model.predict(feat)
        assert 0.0 <= score <= 1.0
        assert not math.isnan(score)
        assert not math.isinf(score)

    def test_model_handles_boundary_values(self):
        """Model should handle extreme (but valid) feature values."""
        model = _load_model()

        # All zeros
        score = model.predict([0.0] * 19)
        assert 0.0 <= score <= 1.0

        # All ones
        score = model.predict([1.0] * 19)
        assert 0.0 <= score <= 1.0

        # Mixed extremes
        feat = [0.0] * 19
        feat[11] = 1.0  # ua_category = bot
        feat[10] = 0.0  # has_accept_language = no
        score = model.predict(feat)
        assert 0.0 <= score <= 1.0
