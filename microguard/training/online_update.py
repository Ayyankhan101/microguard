"""Per-deployment adaptation: capture corrections, fine-tune on them.

    operator clicks "wrong"
      │
      ├─ record_correction()   append one JSONL row, fsync'd
      │                        keyed by decision id, so a double-click is one
      │                        example and a changed mind replaces the old one
      ▼
    microguard retrain
      ├─ load_corrections()    skips unreadable rows, reports how many
      ├─ safety rails          too few, or too one-sided -> raise, write nothing
      ├─ fine-tune the BASELINE, briefly and gently
      └─ os.replace()          atomic publish, never the baseline path

The invariant that matters most: `base_model_path` is never written. One
deployment's bad corrections must not corrupt the model every other deployment,
and every fresh install, starts from. Rolling back is deleting one file.

Feedback lives in a user data directory, not in `data/` -- that ships inside
the wheel and is read-only on a normal install, so writing corrections there
would fail exactly where it is hardest to notice.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..model import DEFAULT_MODEL_PATH, BotDetector

logger = logging.getLogger(__name__)

NUM_FEATURES = 19
DEFAULT_MIN_EXAMPLES = 50
# Above this fraction in either class, the set teaches a constant rather than a
# pattern. Fifty corrections that all say "bot" produce a model that says bot.
DEFAULT_MAX_CLASS_IMBALANCE = 0.90
# Low relative to training/train.py's defaults. This is incremental adaptation
# on top of a trained model, not training from scratch; a long run on a few
# dozen examples would overwrite what the baseline knows.
DEFAULT_EPOCHS = 20
DEFAULT_LEARNING_RATE = 0.01

# The id reaches a filename and arrives over HTTP. Anything outside this set is
# refused rather than sanitized: silently rewriting an id would put corrections
# somewhere the operator is not looking.
_DEPLOYMENT_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class InsufficientFeedbackError(RuntimeError):
    """Not enough confirmed corrections to fine-tune on."""


class ClassImbalanceError(RuntimeError):
    """The corrections are too one-sided to teach a pattern."""


def default_feedback_dir() -> Path:
    """Where corrections accumulate.

    Not inside the package: `data/` ships in the wheel and is read-only on a
    normal install. Same reasoning as the feed caches in live/.
    """
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / "microguard" / "feedback"


def _validate_deployment_id(deployment_id: str) -> str:
    if not _DEPLOYMENT_ID_RE.match(deployment_id or ""):
        raise ValueError(
            f"deployment_id must match {_DEPLOYMENT_ID_RE.pattern!r}, got {deployment_id!r}"
        )
    return deployment_id


def feedback_path(deployment_id: str, feedback_dir: Path | str | None = None) -> Path:
    root = Path(feedback_dir) if feedback_dir is not None else default_feedback_dir()
    return root / f"{_validate_deployment_id(deployment_id)}.jsonl"


def deployment_model_path(deployment_id: str, feedback_dir: Path | str | None = None) -> Path:
    root = Path(feedback_dir) if feedback_dir is not None else default_feedback_dir()
    return root / f"{_validate_deployment_id(deployment_id)}_model.json"


def record_correction(
    deployment_id: str,
    decision_id: str,
    features: list[float],
    confirmed_label: float,
    feedback_dir: Path | str | None = None,
) -> None:
    """Append one operator-confirmed correction.

    Keyed by `decision_id`: a double-click, a retried request, or an operator
    changing their mind all describe one decision with one true label, and
    `load_corrections` keeps the last row for each id. Appending rather than
    rewriting keeps this O(1) on a path a dashboard click waits for.

    A write failure propagates. An operator who clicked "wrong" and saw nothing
    happen would click again; a lost correction has to be visible.
    """
    _validate_deployment_id(deployment_id)
    if len(features) != NUM_FEATURES:
        raise ValueError(f"expected {NUM_FEATURES} features, got {len(features)}")
    if confirmed_label not in (0.0, 1.0):
        raise ValueError(f"label must be 0.0 or 1.0, got {confirmed_label}")

    path = feedback_path(deployment_id, feedback_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "decision_id": decision_id,
        "features": [float(f) for f in features],
        "label": float(confirmed_label),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
        handle.flush()
        # A correction that survives only in the page cache is a correction
        # lost to the next power cut, and there is no second copy.
        os.fsync(handle.fileno())


def load_corrections(
    deployment_id: str,
    feedback_dir: Path | str | None = None,
    report_skipped: bool = False,
):
    """Every correction for one deployment, newest label per decision.

    Unreadable rows are skipped and counted rather than fatal: a truncated
    append -- a disk filling, a process killed mid-write -- must cost one
    example, not the whole training set.
    """
    path = feedback_path(deployment_id, feedback_dir)
    by_decision: dict[str, dict] = {}
    skipped = 0

    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    features = row["features"]
                    label = float(row["label"])
                    decision_id = str(row["decision_id"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    skipped += 1
                    continue
                if not isinstance(features, list) or len(features) != NUM_FEATURES:
                    skipped += 1
                    continue
                by_decision[decision_id] = {
                    "decision_id": decision_id,
                    "features": [float(f) for f in features],
                    "label": label,
                }

    rows = list(by_decision.values())
    return (rows, skipped) if report_skipped else rows


def _copy_normalization(base_model_path: Path, target: Path) -> None:
    """Put the baseline's normalization beside the deployment model.

    `BotDetector.save()` writes weights only, and `load()` reads
    `normalization.json` from the directory beside the model it is given. A
    deployment model published without one gets RAW features at scoring time,
    while having been trained on scaled ones -- and nothing fails. That exact
    mismatch has already cost this project a model with 2.4% held-out bot
    recall instead of 100%, with no exception, no warning, and scores still in
    range. See docs/reference-model.md.

    The scaling belongs to the feature extractor, not to the weights, so the
    baseline's file is the correct one to carry across: fine-tuning changes
    what the network does with a scaled input, never what "scaled" means.
    """
    source = base_model_path.parent / "normalization.json"
    if not source.exists():
        logger.warning(
            "no normalization.json beside %s - the deployment model will score "
            "raw features, which it was not trained on",
            base_model_path,
        )
        return
    destination = target.parent / "normalization.json"
    if destination.resolve() == source.resolve():
        return
    shutil.copyfile(source, destination)


def retrain_deployment_model(
    deployment_id: str,
    base_model_path: Path | str = DEFAULT_MODEL_PATH,
    feedback_dir: Path | str | None = None,
    min_examples: int = DEFAULT_MIN_EXAMPLES,
    max_class_imbalance: float = DEFAULT_MAX_CLASS_IMBALANCE,
    epochs: int = DEFAULT_EPOCHS,
    learning_rate: float = DEFAULT_LEARNING_RATE,
) -> Path:
    """Fine-tune the baseline on one deployment's corrections.

    Both rails are checked BEFORE any training happens and raise rather than
    silently skipping: an operator who ran a retrain needs to know it did not
    happen, and why.

    `base_model_path` is never written. That is the invariant the whole feature
    rests on.
    """
    rows, skipped = load_corrections(deployment_id, feedback_dir, report_skipped=True)
    if skipped:
        logger.warning(
            "%d unreadable correction row(s) skipped for deployment %r",
            skipped, deployment_id,
        )

    if len(rows) < min_examples:
        raise InsufficientFeedbackError(
            f"deployment {deployment_id!r} has {len(rows)} correction(s), "
            f"need at least {min_examples}"
        )

    labels = [row["label"] for row in rows]
    bots = sum(1 for label in labels if label > 0.5)
    share = max(bots, len(labels) - bots) / len(labels)
    if share > max_class_imbalance:
        raise ClassImbalanceError(
            f"deployment {deployment_id!r} corrections are {share:.0%} one class, "
            f"above the {max_class_imbalance:.0%} limit. A one-sided set teaches "
            f"a constant, not a pattern."
        )

    detector = BotDetector()
    detector.load(str(base_model_path))
    detector.train(
        [row["features"] for row in rows],
        labels,
        epochs=epochs,
        learning_rate=learning_rate,
        verbose=False,
    )

    target = deployment_model_path(deployment_id, feedback_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write beside the target, then rename. os.replace is atomic on POSIX and
    # on Windows, so a crash here leaves the previous model intact rather than
    # a half-written file the scorer's mtime watcher would happily load.
    handle, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    os.close(handle)
    tmp_path = Path(tmp_name)
    try:
        detector.save(str(tmp_path))
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)

    _copy_normalization(Path(base_model_path), target)

    logger.warning(
        "deployment %r retrained on %d correction(s) -> %s",
        deployment_id, len(rows), target,
    )
    return target
