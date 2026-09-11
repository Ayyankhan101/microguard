"""Shared heuristic/model score blending — the ONE place this logic lives.

Extracted from cli.py::scan_logfile and watch.py::watch_logfile, which had
duplicated this exact formula independently and drifted out of sync once
already (see CHANGELOG). Both must be refactored to call this instead of
keeping their own inline copies.
"""


# Score above which a request is blocked. Shared by the live scorer, the check
# server, the middleware, and the `microguard serve` CLI so the four cannot
# drift. It lives here, next to the blend it is compared against, and not in
# live/ — importing live/ pulls in redis, and `microguard scan` must keep
# working on a base install with no extras.
#
# 0.85 rather than the spec's 0.7: compute_combined_score floors a confident
# heuristic 'bot' at its own confidence, and the labeler's bot rules sit at
# 0.65-0.95. At 0.7 the 0.75 rules (high request rate) and 0.80 rules block on
# their own; at 0.85 only the 0.90-0.95 rules do, and the model has to agree to
# push anything weaker over the line.
BLOCK_THRESHOLD_DEFAULT = 0.85


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
