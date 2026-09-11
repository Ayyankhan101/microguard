"""Unit tests for the dashboard's evaluation maths.

Kept separate from the API tests so the numbers are checked against
hand-computed values, not against whatever the endpoint happens to return.
"""

import pytest

from microguard.dashboard.metrics import evaluate_scores


class TestEvaluateScores:
    def test_counts_the_confusion_matrix_at_the_given_threshold(self):
        scores = [0.9, 0.8, 0.4, 0.1]
        labels = [1, 0, 1, 0]

        result = evaluate_scores(scores, labels, threshold=0.5)

        assert result["confusion"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}

    def test_a_score_exactly_on_the_threshold_is_not_a_positive(self):
        result = evaluate_scores([0.5], [1], threshold=0.5)

        assert result["confusion"]["tp"] == 0
        assert result["confusion"]["fn"] == 1

    def test_derives_precision_recall_f1_and_accuracy(self):
        # tp=2 fp=1 fn=1 tn=1: precision 2/3, recall 2/3, f1 2/3, accuracy 3/5
        scores = [0.9, 0.8, 0.7, 0.2, 0.1]
        labels = [1, 1, 0, 1, 0]

        result = evaluate_scores(scores, labels, threshold=0.5)

        assert result["precision"] == pytest.approx(2 / 3)
        assert result["recall"] == pytest.approx(2 / 3)
        assert result["f1"] == pytest.approx(2 / 3)
        assert result["accuracy"] == pytest.approx(3 / 5)

    def test_precision_is_zero_when_nothing_is_predicted_positive(self):
        result = evaluate_scores([0.1, 0.2], [1, 0], threshold=0.5)

        assert result["precision"] == 0.0
        assert result["recall"] == 0.0
        assert result["f1"] == 0.0

    def test_roc_curve_spans_from_the_all_positive_to_the_all_negative_corner(self):
        result = evaluate_scores([0.9, 0.1], [1, 0], threshold=0.5)

        roc = result["roc"]
        assert roc[0] == {"fpr": 1.0, "tpr": 1.0}
        assert roc[-1] == {"fpr": 0.0, "tpr": 0.0}

    def test_score_distribution_bins_scores_into_twenty_buckets(self):
        # 20 buckets means each spans 0.05; 1.0 clamps into the last one.
        result = evaluate_scores([0.0, 0.04, 0.99, 1.0], [0, 0, 1, 1], threshold=0.5)

        distribution = result["score_distribution"]
        assert len(distribution) == 20
        assert distribution[0] == 2
        assert distribution[19] == 2

    def test_mismatched_lengths_are_a_programming_error(self):
        with pytest.raises(ValueError):
            evaluate_scores([0.1], [1, 0], threshold=0.5)
