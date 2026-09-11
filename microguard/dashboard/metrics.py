"""Classification metrics for the dashboard's model tab.

Pure functions over scores and labels — no model, no I/O — so the numbers can
be checked against hand-computed values.
"""

from __future__ import annotations

SCORE_BUCKETS = 20


def _counts(scores: list[float], labels: list[int], threshold: float) -> dict[str, int]:
    tp = fp = fn = tn = 0
    for score, label in zip(scores, labels):
        # Strict `>`, matching LiveScorer's block test (live/scorer.py:147): a
        # score sitting exactly on the bar has not cleared it.
        predicted = score > threshold
        if label:
            tp, fn = (tp + 1, fn) if predicted else (tp, fn + 1)
        else:
            fp, tn = (fp + 1, tn) if predicted else (fp, tn + 1)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _roc(scores: list[float], labels: list[int]) -> list[dict[str, float]]:
    """The full ROC curve, swept over every score as a candidate threshold."""
    positives = sum(1 for label in labels if label)
    negatives = len(labels) - positives
    points = []
    for candidate in sorted({*scores, -1.0, 1.0}):
        counts = _counts(scores, labels, candidate)
        points.append(
            {
                "fpr": counts["fp"] / negatives if negatives else 0.0,
                "tpr": counts["tp"] / positives if positives else 0.0,
            }
        )
    return points


def evaluate_scores(
    scores: list[float], labels: list[int], threshold: float
) -> dict:
    """Confusion matrix, derived rates, ROC curve and score histogram."""
    if len(scores) != len(labels):
        raise ValueError(
            f"scores and labels must be the same length, got {len(scores)} and {len(labels)}"
        )

    counts = _counts(scores, labels, threshold)
    tp, fp, fn, tn = counts["tp"], counts["fp"], counts["fn"], counts["tn"]

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total = tp + fp + fn + tn

    distribution = [0] * SCORE_BUCKETS
    for score in scores:
        bucket = min(int(score * SCORE_BUCKETS), SCORE_BUCKETS - 1)
        distribution[bucket] += 1

    return {
        "confusion": counts,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": (tp + tn) / total if total else 0.0,
        "roc": _roc(scores, labels),
        "score_distribution": distribution,
        "n_samples": total,
        "threshold": threshold,
    }
