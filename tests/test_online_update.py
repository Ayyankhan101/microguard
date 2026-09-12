"""Tests for per-deployment feedback capture and retraining.

The single most important assertion in this file is that the shipped baseline
model is never written. One deployment's bad corrections must not corrupt the
model every other deployment, and every fresh install, starts from.
"""

import hashlib
import json
import random

import pytest

from microguard.model import BotDetector
from microguard.training.online_update import (
    ClassImbalanceError,
    InsufficientFeedbackError,
    default_feedback_dir,
    deployment_model_path,
    load_corrections,
    record_correction,
    retrain_deployment_model,
)


def _features(seed: float) -> list[float]:
    return [seed] * 19


def _record_many(tmp_path, deployment, count, label=1.0, start=0):
    for i in range(count):
        record_correction(
            deployment_id=deployment,
            decision_id=f"d{start + i}",
            features=_features((start + i) / 100.0),
            confirmed_label=label,
            feedback_dir=tmp_path,
        )


class TestFeedbackDirectory:
    def test_it_is_not_inside_the_installed_package(self, monkeypatch, tmp_path):
        """`data/` ships in the wheel and is read-only on a normal install, so
        a feedback file written there fails exactly where it is hardest to
        notice -- a root-owned or system install."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        resolved = default_feedback_dir()

        assert str(resolved).startswith(str(tmp_path))
        assert "site-packages" not in str(resolved)

    def test_it_falls_back_to_a_home_directory(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        assert default_feedback_dir() == tmp_path / ".local" / "share" / "microguard" / "feedback"


class TestRecordCorrection:
    def test_a_correction_is_appended_as_jsonl(self, tmp_path):
        record_correction("prod", "d1", _features(0.5), 1.0, feedback_dir=tmp_path)

        rows = (tmp_path / "prod.jsonl").read_text(encoding="utf-8").strip().splitlines()
        row = json.loads(rows[0])
        assert row["decision_id"] == "d1"
        assert row["label"] == 1.0
        assert len(row["features"]) == 19
        assert row["timestamp"]

    def test_deployments_never_read_each_others_corrections(self, tmp_path):
        """The whole point is adapting to ONE deployment's traffic. Mixing two
        would produce a model that fits neither."""
        record_correction("prod", "d1", _features(0.1), 1.0, feedback_dir=tmp_path)
        record_correction("staging", "d2", _features(0.9), 0.0, feedback_dir=tmp_path)

        assert len(load_corrections("prod", tmp_path)) == 1
        assert load_corrections("prod", tmp_path)[0]["decision_id"] == "d1"
        assert load_corrections("staging", tmp_path)[0]["decision_id"] == "d2"

    def test_correcting_the_same_decision_twice_yields_one_example(self, tmp_path):
        """A double-click, or an operator changing their mind. Either way the
        decision has one true label, not two."""
        record_correction("prod", "d1", _features(0.5), 1.0, feedback_dir=tmp_path)
        record_correction("prod", "d1", _features(0.5), 0.0, feedback_dir=tmp_path)

        rows = load_corrections("prod", tmp_path)
        assert len(rows) == 1
        assert rows[0]["label"] == 0.0, "the later correction wins"

    def test_a_wrong_feature_count_is_refused(self, tmp_path):
        """Nineteen is the model's input width. A short vector would train
        silently and score nonsense forever after."""
        with pytest.raises(ValueError, match="19"):
            record_correction("prod", "d1", [0.1] * 5, 1.0, feedback_dir=tmp_path)

    def test_a_label_that_is_not_zero_or_one_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="label"):
            record_correction("prod", "d1", _features(0.5), 0.5, feedback_dir=tmp_path)

    def test_a_deployment_id_that_would_escape_the_directory_is_refused(self, tmp_path):
        """The id reaches a filename. It arrives over HTTP."""
        for bad in ("../etc/passwd", "a/b", "..", ""):
            with pytest.raises(ValueError, match="deployment_id"):
                record_correction(bad, "d1", _features(0.5), 1.0, feedback_dir=tmp_path)

    def test_a_write_failure_surfaces_rather_than_being_swallowed(self, tmp_path):
        """An operator who clicked 'wrong' and saw nothing happen would keep
        clicking. A lost correction has to be visible."""
        blocked = tmp_path / "file"
        blocked.write_text("not a directory", encoding="utf-8")

        with pytest.raises(OSError):
            record_correction("prod", "d1", _features(0.5), 1.0, feedback_dir=blocked)


class TestLoadCorrections:
    def test_an_unparseable_line_is_skipped_and_counted(self, tmp_path):
        """A truncated append -- a disk filling, a process killed mid-write --
        must cost one example, not the whole training set."""
        _record_many(tmp_path, "prod", 2)
        with (tmp_path / "prod.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{truncated\n")
        _record_many(tmp_path, "prod", 1, start=99)

        rows, skipped = load_corrections("prod", tmp_path, report_skipped=True)
        assert len(rows) == 3
        assert skipped == 1

    def test_a_row_missing_fields_is_skipped(self, tmp_path):
        (tmp_path / "prod.jsonl").write_text(
            json.dumps({"decision_id": "d1"}) + "\n", encoding="utf-8"
        )
        rows, skipped = load_corrections("prod", tmp_path, report_skipped=True)
        assert rows == []
        assert skipped == 1

    def test_an_absent_file_is_empty_not_an_error(self, tmp_path):
        assert load_corrections("never-seen", tmp_path) == []


class TestSafetyRails:
    def test_too_few_examples_raises_and_writes_nothing(self, tmp_path):
        _record_many(tmp_path, "prod", 5)

        with pytest.raises(InsufficientFeedbackError, match="5"):
            retrain_deployment_model("prod", feedback_dir=tmp_path, min_examples=50)

        assert not deployment_model_path("prod", tmp_path).exists()

    def test_a_one_sided_feedback_set_raises_and_writes_nothing(self, tmp_path):
        """Fifty corrections that all say 'bot' teach the model to say bot."""
        _record_many(tmp_path, "prod", 50, label=1.0)

        with pytest.raises(ClassImbalanceError):
            retrain_deployment_model("prod", feedback_dir=tmp_path, min_examples=10)

        assert not deployment_model_path("prod", tmp_path).exists()

    def test_a_balanced_set_passes_the_rails(self, tmp_path):
        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)

        path = retrain_deployment_model("prod", feedback_dir=tmp_path, min_examples=10, epochs=2)
        assert path.exists()


class TestBaselineImmutability:
    def test_retraining_never_writes_the_baseline(self, tmp_path):
        """The single most important invariant here. One deployment's bad
        corrections must not corrupt what every other deployment, and every
        fresh install, starts from."""
        baseline = tmp_path / "baseline.json"
        BotDetector().save(str(baseline))
        before = hashlib.sha256(baseline.read_bytes()).hexdigest()

        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)
        retrain_deployment_model(
            "prod", base_model_path=baseline, feedback_dir=tmp_path,
            min_examples=10, epochs=2,
        )

        assert hashlib.sha256(baseline.read_bytes()).hexdigest() == before

    def test_the_deployment_model_is_written_somewhere_else_entirely(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        BotDetector().save(str(baseline))
        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)

        written = retrain_deployment_model(
            "prod", base_model_path=baseline, feedback_dir=tmp_path,
            min_examples=10, epochs=2,
        )
        assert written != baseline


class TestAtomicPublish:
    def test_a_crash_mid_write_leaves_the_previous_model_intact(self, tmp_path, monkeypatch):
        """os.replace is atomic on POSIX. Without it a killed retrain leaves a
        half-written file that the scorer's mtime watcher happily picks up."""
        baseline = tmp_path / "baseline.json"
        BotDetector().save(str(baseline))
        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)

        good = retrain_deployment_model(
            "prod", base_model_path=baseline, feedback_dir=tmp_path,
            min_examples=10, epochs=2,
        )
        original = good.read_bytes()

        def boom(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr("microguard.training.online_update.os.replace", boom)
        with pytest.raises(KeyboardInterrupt):
            retrain_deployment_model(
                "prod", base_model_path=baseline, feedback_dir=tmp_path,
                min_examples=10, epochs=2,
            )

        assert good.read_bytes() == original

    def test_no_temp_file_is_left_behind(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        BotDetector().save(str(baseline))
        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)
        retrain_deployment_model(
            "prod", base_model_path=baseline, feedback_dir=tmp_path,
            min_examples=10, epochs=2,
        )

        assert list(tmp_path.glob("*.tmp*")) == []


class TestItActuallyLearns:
    def test_the_retrained_model_beats_the_baseline_on_its_own_pattern(self, tmp_path):
        """The value proposition, measured rather than assumed.

        The corrections describe a pattern the baseline gets wrong: sessions
        the shipped model scores one way that this deployment has confirmed go
        the other. If fine-tuning does not move accuracy on that set, the whole
        feature is theatre.
        """
        random.seed(20260912)
        baseline_path = tmp_path / "baseline.json"
        BotDetector().save(str(baseline_path))
        baseline = BotDetector(str(baseline_path))

        # Build a learnable, deliberately contrarian set: label each example
        # as the OPPOSITE of whatever the baseline currently says.
        rows = []
        for i in range(60):
            features = [random.random() for _ in range(19)]
            said_bot = baseline.predict(features) > 0.5
            rows.append((features, 0.0 if said_bot else 1.0))

        for i, (features, label) in enumerate(rows):
            record_correction("prod", f"d{i}", features, label, feedback_dir=tmp_path)

        def accuracy(detector):
            correct = sum(
                1 for f, y in rows if (detector.predict(f) > 0.5) == (y > 0.5)
            )
            return correct / len(rows)

        before = accuracy(baseline)
        path = retrain_deployment_model(
            "prod", base_model_path=baseline_path, feedback_dir=tmp_path,
            min_examples=10, epochs=60, learning_rate=0.05,
        )
        after = accuracy(BotDetector(str(path)))

        assert after > before, f"no learning: {before:.2f} -> {after:.2f}"


class TestNormalizationTravelsWithTheModel:
    """`save()` writes weights only; `load()` reads normalization.json from the
    directory beside the model. A deployment model published without one scores
    RAW features while having been trained on scaled ones -- and nothing fails.
    That mismatch already cost this project a model with 2.4% held-out recall
    instead of 100%, silently. See docs/reference-model.md.
    """

    def _retrain(self, tmp_path, baseline):
        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)
        return retrain_deployment_model(
            "prod", base_model_path=baseline, feedback_dir=tmp_path,
            min_examples=10, epochs=2,
        )

    def test_the_baselines_normalization_is_copied_beside_the_new_model(self, tmp_path):
        model_dir = tmp_path / "shipped"
        model_dir.mkdir()
        baseline = model_dir / "baseline.json"
        BotDetector().save(str(baseline))
        (model_dir / "normalization.json").write_text(
            json.dumps({"mins": [0.0] * 19, "maxs": [1.0] * 19}), encoding="utf-8"
        )

        written = self._retrain(tmp_path, baseline)

        assert (written.parent / "normalization.json").exists()

    def test_the_retrained_model_loads_its_normalization(self, tmp_path):
        model_dir = tmp_path / "shipped"
        model_dir.mkdir()
        baseline = model_dir / "baseline.json"
        BotDetector().save(str(baseline))
        (model_dir / "normalization.json").write_text(
            json.dumps({"mins": [0.0] * 19, "maxs": [2.0] * 19}), encoding="utf-8"
        )

        written = self._retrain(tmp_path, baseline)
        reloaded = BotDetector(str(written))

        assert reloaded.norm_maxs == [2.0] * 19

    def test_a_baseline_without_normalization_warns_rather_than_failing(self, tmp_path, caplog):
        baseline = tmp_path / "baseline.json"
        BotDetector().save(str(baseline))

        self._retrain(tmp_path, baseline)

        assert "no normalization.json" in caplog.text


class TestLoadingEdgeCases:
    def test_blank_lines_are_ignored(self, tmp_path):
        """A file ending in a newline, or appended to after a crash, has them."""
        _record_many(tmp_path, "prod", 1)
        with (tmp_path / "prod.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("\n   \n")

        rows, skipped = load_corrections("prod", tmp_path, report_skipped=True)
        assert len(rows) == 1
        assert skipped == 0

    def test_a_row_with_the_wrong_feature_count_is_skipped(self, tmp_path):
        """Nineteen is the model's input width. Training on a short vector
        would fail or, worse, silently misalign every feature after the gap."""
        (tmp_path / "prod.jsonl").write_text(
            json.dumps({"decision_id": "d1", "features": [0.1] * 5, "label": 1.0}) + "\n",
            encoding="utf-8",
        )
        rows, skipped = load_corrections("prod", tmp_path, report_skipped=True)
        assert rows == []
        assert skipped == 1

    def test_a_row_whose_features_are_not_a_list_is_skipped(self, tmp_path):
        (tmp_path / "prod.jsonl").write_text(
            json.dumps({"decision_id": "d1", "features": "nineteen", "label": 1.0}) + "\n",
            encoding="utf-8",
        )
        assert load_corrections("prod", tmp_path) == []

    def test_a_retrain_reports_how_many_rows_it_skipped(self, tmp_path, caplog):
        """Silence here would let a slowly corrupting file shrink the training
        set with nobody noticing."""
        baseline = tmp_path / "baseline.json"
        BotDetector().save(str(baseline))
        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)
        with (tmp_path / "prod.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{truncated\n")

        retrain_deployment_model(
            "prod", base_model_path=baseline, feedback_dir=tmp_path,
            min_examples=10, epochs=2,
        )

        assert "1 unreadable correction row(s) skipped" in caplog.text

    def test_a_baseline_already_beside_the_target_is_not_self_copied(self, tmp_path):
        """When the baseline and the deployment model share a directory,
        copying normalization onto itself would truncate the file."""
        baseline = tmp_path / "baseline.json"
        BotDetector().save(str(baseline))
        norm = tmp_path / "normalization.json"
        norm.write_text(json.dumps({"mins": [0.0] * 19, "maxs": [3.0] * 19}), encoding="utf-8")

        _record_many(tmp_path, "prod", 25, label=1.0)
        _record_many(tmp_path, "prod", 25, label=0.0, start=100)
        retrain_deployment_model(
            "prod", base_model_path=baseline, feedback_dir=tmp_path,
            min_examples=10, epochs=2,
        )

        assert json.loads(norm.read_text(encoding="utf-8"))["maxs"] == [3.0] * 19
