"""Model endpoints — the trained network and its evaluation on the stored sets."""

from __future__ import annotations

import functools
import json
import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..features import FEATURE_NAMES
from ..model import DEFAULT_MODEL_PATH, BotDetector
from .metrics import evaluate_scores
from .paths import DATA_DIR

router = APIRouter(prefix="/api/model", tags=["model"])

# The evaluation sets shipped in data/. Names are a closed set, so a dataset
# argument can never become a path.
DATASETS = {
    "holdout": "eval_holdout.json",
    "adversarial": "adversarial_eval.json",
}

NORMALIZATION_PATH = os.path.join(os.path.dirname(DEFAULT_MODEL_PATH), "normalization.json")


class EvaluateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    dataset: Literal["holdout", "adversarial"]
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@functools.lru_cache(maxsize=1)
def _detector() -> BotDetector:
    """One detector for the process. predict() only reads parameters."""
    if not os.path.exists(DEFAULT_MODEL_PATH):
        raise HTTPException(status_code=503, detail="No trained model available")
    return BotDetector(DEFAULT_MODEL_PATH)


@functools.lru_cache(maxsize=len(DATASETS))
def _scores(dataset: str) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Score one evaluation set. Cached: 623 pure-Python forward passes is not
    something to redo on every threshold drag in the UI."""
    payload = _read_json(os.path.join(DATA_DIR, DATASETS[dataset]))
    scored = _detector().predict_batch(payload["features"])
    return tuple(scored), tuple(payload["labels"])


@router.get("")
def describe_model() -> dict:
    """The trained network as stored, plus the ranges predict() normalizes with."""
    if not os.path.exists(DEFAULT_MODEL_PATH):
        raise HTTPException(status_code=503, detail="No trained model available")
    model = _read_json(DEFAULT_MODEL_PATH)
    normalization = (
        _read_json(NORMALIZATION_PATH) if os.path.exists(NORMALIZATION_PATH) else None
    )
    return {
        "num_features": model["num_features"],
        "architecture": model["architecture"],
        "feature_names": FEATURE_NAMES,
        "weights": model["weights"],
        "normalization": normalization,
    }


@router.post("/evaluate")
def evaluate(request: EvaluateRequest) -> dict:
    """Evaluate the trained model on a stored set at the given threshold."""
    scores, labels = _scores(request.dataset)
    result = evaluate_scores(list(scores), list(labels), request.threshold)
    result["dataset"] = request.dataset
    return result
