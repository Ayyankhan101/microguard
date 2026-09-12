# Spec 0004: Per-deployment adaptive learning

Epic: [0000-epic-paid-alternative-parity.md](0000-epic-paid-alternative-parity.md)
Depends on: [0001-real-time-blocking.md](0001-real-time-blocking.md) (must be merged first)
Status: **SHIPPED** — v3.0.0 (`29aaf79`), with deliberate deviations

> **Read this before planning against the code below.**
>
> | Spec says | What exists |
> |---|---|
> | `record_outcome(...)` | `record_correction(...)`, keyed by **decision id** so a double-click or a changed mind is one example with one label |
> | `feedback_dir="data/live_feedback"` | `$XDG_DATA_HOME/microguard/feedback` — `data/` ships in the wheel and is read-only on a normal install |
> | look the feature vector up from the session store | The vector is stored **on the decision** at scoring time. The live session is a 200-entry sliding window on a 1800s TTL, so by the time anyone reviews a block the inputs are gone |
> | `score_live_request(deployment_id=...)` | `LiveScorer(deployment_id=..., feedback_dir=...)` |
> | a dashboard UI is out of scope | It shipped. A dashboard existed by then, and `min_examples=50` makes CLI-only collection equivalent to no feature |
>
> **What this spec missed, found while building:** `BotDetector.save()` writes
> weights only, and `load()` reads `normalization.json` from the directory
> beside the model. A deployment model published without one scores **raw**
> features while trained on scaled ones — the same mismatch that once left this
> project at 2.4% held-out recall instead of 100%, silently. The baseline's
> normalization now travels with every deployment model.
>
> The safety rails and the baseline-immutability invariant shipped exactly as
> specified, and the immutability one is asserted by hashing the baseline file
> before and after a retrain.
Priority: Medium
Effort estimate: 3-4 days

## Context

`data/model.json` is one model trained once on the training data described in
`CHANGELOG.md`'s `[2.0.0]` entry — every deployment runs the identical
weights. A generic paid vendor model has the same limitation across all its
customers. A self-hosted tool has an opening a paid vendor structurally
doesn't: it can adapt to ONE specific deployment's own real traffic pattern
over time, which is where it can genuinely end up with fewer false positives
than a one-size-fits-all model — this is the actual "better, not just free"
argument for this whole epic, not merely cost positioning.

## Current State (verified 2026-09-06)

- `microguard/model.py::BotDetector` (`model.py:23`) already has
  `.train_step(features_batch, labels, learning_rate)` and
  `.train(features, labels, epochs, ...)` — the fine-tuning primitives exist,
  built for the offline `training/train.py` pipeline. This spec reuses them
  for online/incremental updates — it does not need new training math.
- `microguard/training/train.py::split_holdout` (search in that file) already
  implements the stratified-group-split pattern this spec's safety rails
  should mirror for consistency (same repo, same conventions).
- There is no mechanism today to capture a human-confirmed correction to a
  live classification, and no per-deployment model file concept —
  `data/model.json` is the only model file path referenced anywhere in
  `cli.py`/`watch.py`/(spec 0001's) `live/scorer.py`.
- This spec depends on spec 0001 because there's no live scoring/outcome
  stream to learn from before that exists — a batch log file has no natural
  "an operator confirmed this was wrong" feedback loop the way a live system
  does.

## Proposed Change

### 1. Feedback capture

New file `microguard/training/online_update.py`:
```python
def record_outcome(
    deployment_id: str,
    features: list[float],
    confirmed_label: float,  # 0.0 human, 1.0 bot
    feedback_dir: str = "data/live_feedback",
) -> None:
    """Append one JSONL line to {feedback_dir}/{deployment_id}.jsonl:
    {"features": [...], "label": confirmed_label, "timestamp": iso8601}.
    One file per deployment — never mixes feedback across deployments,
    since the whole point is per-deployment adaptation."""
```

New CLI command (extends `microguard/cli.py`'s existing subparser pattern):
```
microguard feedback <session-id-or-ip> --label human|bot --deployment-id <id>
```
Requires spec 0001's live session store to look up the session's stored
feature vector by ID/IP — add a method to `RedisSessionStateStore` (or
`live/scorer.py`) to retrieve a recently-scored session's feature vector for
this purpose; decide the exact lookup key during implementation (likely the
same `(ip, user_agent)` key spec 0001 already uses).

Also expose the same capability as an admin API endpoint on
`microguard/live/server.py` (`POST /feedback`) for programmatic use (e.g. a
dashboard button an operator clicks), not just the CLI — same underlying
`record_outcome()` call.

### 2. Retraining

New function, same file:
```python
def retrain_deployment_model(
    deployment_id: str,
    base_model_path: str = "data/model.json",
    feedback_dir: str = "data/live_feedback",
    min_examples: int = 50,
    max_class_imbalance: float = 0.90,
    epochs: int = 20,
    learning_rate: float = 0.01,
) -> str:
    """Load the baseline model (BotDetector(base_model_path)), fine-tune
    (low epochs, low LR relative to training/train.py's defaults — this is
    incremental adaptation, not retraining from scratch) on ONLY this
    deployment's accumulated feedback JSONL, save to
    {feedback_dir}/{deployment_id}_model.json.

    Safety rails (both must be checked BEFORE calling model.train(), and
    both must raise a clear, documented exception if violated — never
    silently skip or silently train on insufficient/skewed data):
    - Fewer than min_examples confirmed examples -> raise, don't train.
    - Either class exceeds max_class_imbalance fraction of the total ->
      raise, don't train (mirrors the stratification concern
      training/train.py::split_holdout already applies at the offline
      training stage — same reasoning, applied here).

    NEVER writes to base_model_path — the shipped baseline is immutable
    from this function's perspective, always. This is the single most
    important invariant in this spec: one deployment's bad feedback must
    never corrupt the model every other deployment (or a fresh install)
    starts from.

    Returns the path written."""
```

### 3. Model selection at scoring time

`microguard/live/scorer.py`'s `score_live_request` (from spec 0001) gains a
`deployment_id: str | None = None` parameter. If provided AND
`{feedback_dir}/{deployment_id}_model.json` exists, load and use that model
instead of the baseline; otherwise fall back to `base_model_path`. Document
this fallback explicitly wherever `score_live_request` is documented (spec
0001's files + this spec's addition) — a silent, undocumented model swap
is exactly the kind of thing that causes confusing, hard-to-debug behavior
differences between deployments.

Cache the loaded deployment model in-process (don't reload from disk on
every request) with an explicit invalidation trigger: reload if the model
file's mtime changed since last load (simple polling check, no new
dependency needed).

### 4. Retraining trigger

Do NOT auto-retrain on every single feedback submission (expensive, and
risks thrashing the model based on one-off corrections). `microguard serve`
gains a `--retrain-interval-hours <N>` flag (default: retraining is
OFF/manual-only unless explicitly enabled) that, if set, checks once per
interval whether `min_examples` new feedback rows have accumulated since the
last retrain for each deployment_id seen, and if so calls
`retrain_deployment_model`. Also expose `microguard retrain
--deployment-id <id>` as a manual, on-demand CLI command — this should be
the primary supported path for v1; the automatic interval-based path can be
marked explicitly experimental in the README.

## Acceptance Criteria

1. `record_outcome` appends valid, parseable JSONL; two different
   `deployment_id`s never write to or read from each other's file
   (verified by writing to both and asserting each file contains only its
   own deployment's rows).
2. `retrain_deployment_model` raises (a specific, named exception class —
   e.g. `InsufficientFeedbackError`) and does NOT write any model file when
   called with fewer than `min_examples` rows.
3. `retrain_deployment_model` raises (`ClassImbalanceError` or similar) and
   does NOT write any model file when the feedback is more skewed than
   `max_class_imbalance`.
4. `retrain_deployment_model`, given a valid balanced feedback set, writes
   `{deployment_id}_model.json` and does NOT modify `base_model_path`'s file
   (assert the baseline file's mtime/hash is unchanged before/after).
5. The retrained deployment model, evaluated on its own feedback set,
   achieves higher accuracy on that deployment's specific traffic pattern
   than the unmodified baseline model does on the same set — this is the
   actual value proposition, must be measured and asserted in a test, not
   assumed. Use a synthetic feedback set with a clear, learnable pattern the
   baseline model would get wrong (e.g. a traffic pattern near the
   baseline's decision boundary) to make this a meaningful, non-trivial
   assertion rather than a coin-flip comparison.
6. `score_live_request(deployment_id="x")` uses the deployment-specific
   model when present, and correctly falls back to baseline when absent —
   verified both ways in the same test file.
7. `score_live_request` reloads a deployment model after its file's mtime
   changes (simulates a retrain happening while the server is running) —
   verified without restarting the process.
8. `microguard retrain --deployment-id <id>` CLI command runs end-to-end
   against a real feedback file and produces the expected model file.

## Testing Plan

| Layer | What | Count |
|-------|------|-------|
| Unit | `record_outcome` per-deployment file isolation | +2 |
| Unit | `retrain_deployment_model` safety rails (both) | +2 |
| Unit | `retrain_deployment_model` baseline immutability | +1 |
| Unit | Retrained model measurably improves on its own deployment's pattern vs. baseline | +1 |
| Unit | `score_live_request` deployment-model selection + fallback | +2 |
| Unit | mtime-based cache invalidation/reload | +1 |
| Integration | `microguard feedback` CLI → `POST /feedback` → `microguard retrain` end-to-end | +2 |

## Rollback Plan

Delete `{feedback_dir}/{deployment_id}_model.json` — `score_live_request`
falls back to the unmodified baseline immediately (per acceptance criterion
6, this path is already tested and guaranteed to work). The baseline model
was never touched, so this is a fully safe, instant rollback with zero risk
to any other deployment.

## Effort Estimate

| Component | Estimate |
|-----------|----------|
| `record_outcome` + feedback CLI/API | 0.5 day |
| `retrain_deployment_model` + safety rails | 1 day |
| `score_live_request` model selection + caching/reload | 1 day |
| Retrain-interval flag + manual CLI command | 0.5 day |
| Tests (incl. the "measurably improves" test, which needs a carefully
  constructed synthetic case) | 1 day |
| **Total** | **~4 days** |

## Files Reference

| File | Change |
|------|--------|
| `microguard/training/online_update.py` | New — `record_outcome`, `retrain_deployment_model`, exception classes |
| `microguard/live/scorer.py` | Add `deployment_id` param, model caching/fallback logic (extends spec 0001) |
| `microguard/live/server.py` | Add `POST /feedback` (extends spec 0001) |
| `microguard/cli.py` | Add `feedback` and `retrain` subcommands |
| `tests/test_online_update.py` | New |
| `README.md` | New subsection under "Real-Time Blocking" — feedback loop, retraining, the baseline-immutability guarantee |

## Out of Scope

- A dashboard/UI for submitting feedback (CLI + API endpoint only, per this
  spec — a UI is a separate future spec if wanted).
- Federated/cross-deployment learning (each deployment's adaptation stays
  strictly local — see the epic's stance on not chasing a shared network).
- Automated feedback (e.g. inferring "this was wrong" from user behavior
  without an explicit operator confirmation) — all feedback in v1 is
  explicit and human-confirmed, never inferred.

## Related

- Epic: [0000-epic-paid-alternative-parity.md](0000-epic-paid-alternative-parity.md)
- `microguard/model.py::BotDetector.train`/`.train_step` — reused directly.
- `microguard/training/train.py::split_holdout` — the stratification pattern
  this spec's safety rails mirror.
