"""Shared heuristic/model score blending — the ONE place this logic lives.

Extracted from cli.py::scan_logfile and watch.py::watch_logfile, which had
duplicated this exact formula independently and drifted out of sync once
already (see CHANGELOG). Both must be refactored to call this instead of
keeping their own inline copies.
"""


def compute_combined_score(
    heuristic_label: str,
    heuristic_confidence: float,
    model_score: float,
) -> float:
    """Blend heuristic + model scores: 60% model / 40% heuristic.

    A confident heuristic 'bot' call floors the score up to at least its own
    confidence (a strong rule shouldn't be diluted by a weak model score).
    A confident heuristic 'human' call symmetrically caps the score down —
    without this, the model's independent score can override a heuristic
    that correctly recognizes e.g. a single-endpoint API session as human.
    """
    combined = 0.6 * model_score + 0.4 * heuristic_confidence
    if heuristic_label == 'bot':
        combined = max(combined, heuristic_confidence)
    elif heuristic_label == 'human':
        combined = min(combined, 1.0 - heuristic_confidence)
    return combined
