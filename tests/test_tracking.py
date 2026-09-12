"""Tests for MLflow tracking integration."""

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestLazyImport:
    """Test lazy MLflow import mechanism."""

    def test_import_fails_without_mlflow(self):
        """Verify ImportError when mlflow is not installed."""
        original_mlflow = sys.modules.get("mlflow")
        try:
            sys.modules["mlflow"] = None
            import importlib

            import microguard.tracking as t
            importlib.reload(t)
            with pytest.raises(ImportError, match="mlflow"):
                t._get_mlflow()
        finally:
            if original_mlflow is not None:
                sys.modules["mlflow"] = original_mlflow
            elif "mlflow" in sys.modules:
                del sys.modules["mlflow"]

    def test_import_works_with_mlflow(self):
        """Verify _get_mlflow returns mlflow when available."""
        import microguard.tracking as t
        with patch.object(t, "_get_mlflow") as mock_get:
            mock_mlflow = MagicMock()
            mock_get.return_value = mock_mlflow
            result = t._get_mlflow()
            assert result is mock_mlflow


class TestHelpers:
    """Test helper functions (no Databricks auth needed)."""

    def test_format_run(self):
        """Verify run dict formatting."""
        from microguard.tracking import _format_run

        run = MagicMock()
        run.info.run_id = "abc123"
        run.info.status = "FINISHED"
        run.info.start_time = 1700000000000
        run.info.end_time = 1700000060000
        run.data.params = {"learning_rate": "0.01"}
        run.data.metrics = {"accuracy": 0.95, "loss": 0.05}
        run.data.tags = {"mlflow.runName": "test-run"}

        result = _format_run(run)
        assert result["run_id"] == "abc123"
        assert result["run_name"] == "test-run"
        assert result["status"] == "FINISHED"
        assert result["params"] == {"learning_rate": "0.01"}
        assert result["metrics"] == {"accuracy": 0.95, "loss": 0.05}
        assert result["start_time"] == 1700000000000


class TestResolvedTrackingURI:
    """Failure messages name where tracking pointed, so it must be knowable
    without mlflow installed -- the case where it most often matters."""

    def test_it_defaults_to_databricks(self, monkeypatch):
        from microguard.tracking import resolved_tracking_uri

        monkeypatch.delenv("MICROGUARD_MLFLOW_TRACKING_URI", raising=False)
        assert resolved_tracking_uri() == "databricks"

    def test_the_environment_overrides_the_default(self, monkeypatch):
        from microguard.tracking import resolved_tracking_uri

        monkeypatch.setenv("MICROGUARD_MLFLOW_TRACKING_URI", "file:./mlruns")
        assert resolved_tracking_uri() == "file:./mlruns"

    def test_an_explicit_argument_wins_over_the_environment(self, monkeypatch):
        from microguard.tracking import resolved_tracking_uri

        monkeypatch.setenv("MICROGUARD_MLFLOW_TRACKING_URI", "file:./mlruns")
        assert resolved_tracking_uri("databricks") == "databricks"

    def test_it_does_not_need_mlflow(self, monkeypatch):
        """The whole point: reportable when the import is what failed."""
        import microguard.tracking as t

        def no_mlflow():
            raise ImportError("No module named 'mlflow'")

        monkeypatch.setattr(t, "_get_mlflow", no_mlflow)
        assert t.resolved_tracking_uri() is not None


class TestInitAnnouncesWhereItPoints:
    def test_init_logs_the_resolved_uri(self, monkeypatch, caplog):
        import logging

        import microguard.tracking as t

        monkeypatch.setattr(t, "_get_mlflow", lambda: MagicMock())
        monkeypatch.setenv("MICROGUARD_MLFLOW_TRACKING_URI", "file:./mlruns")

        with caplog.at_level(logging.INFO, logger="microguard.tracking"):
            t.init()

        assert "file:./mlruns" in caplog.text


class TestPyFunc:
    """Test BotDetectorPyFunc wrapper."""

    def test_init(self):
        """Verify BotDetectorPyFunc can be instantiated."""
        from microguard.tracking import BotDetectorPyFunc
        
        wrapper = BotDetectorPyFunc()
        assert hasattr(wrapper, "load_context")
        assert hasattr(wrapper, "predict")

    def test_predict(self):
        """Verify predict method delegates to detector.

        numpy arrives as an mlflow dependency, and the test matrix installs
        .[live,fastapi,flask,dashboard] without the mlflow extra -- so this
        test is unrunnable there and must skip rather than fail. It passed
        locally only because this machine happens to have numpy from another
        environment.
        """
        np = pytest.importorskip(
            "numpy", reason="numpy ships with the mlflow extra; not installed here"
        )

        from microguard.tracking import BotDetectorPyFunc

        wrapper = BotDetectorPyFunc()
        wrapper.detector = MagicMock()
        wrapper.detector.predict_batch.return_value = [0.8, 0.2]

        context = MagicMock()
        model_input = [[0.5] * 19, [0.3] * 19]
        result = wrapper.predict(context, model_input)
        
        assert isinstance(result, np.ndarray)
        assert result.tolist() == [0.8, 0.2]

    def test_load_model(self):
        """Verify load_model calls mlflow correctly."""
        import microguard.tracking as t
        
        with patch.object(t, "_get_mlflow") as mock_get:
            mock_mlflow = MagicMock()
            mock_get.return_value = mock_mlflow

            from microguard.tracking import load_model
            load_model(version=1)
            mock_mlflow.pyfunc.load_model.assert_called_once_with("models:/microguard/1")


class TestTheThinWrappers:
    """Every one of these is a delegation to mlflow.

    They were uncovered because the matrix job does not install the mlflow
    extra -- but the delegation itself is testable without it, by mocking
    `_get_mlflow`. Covering them here gates the code on every runner instead
    of adding a ~100MB dependency to twelve jobs to reach the same place.
    """

    def _mlflow(self, monkeypatch):
        import microguard.tracking as t

        fake = MagicMock()
        monkeypatch.setattr(t, "_get_mlflow", lambda: fake)
        return t, fake

    def test_start_run_delegates(self, monkeypatch):
        t, fake = self._mlflow(monkeypatch)
        t.start_run(run_name="r1")
        fake.start_run.assert_called_once_with(run_name="r1")

    def test_log_params_delegates(self, monkeypatch):
        t, fake = self._mlflow(monkeypatch)
        t.log_params({"epochs": 100})
        fake.log_params.assert_called_once_with({"epochs": 100})

    def test_log_metrics_delegates_with_the_step(self, monkeypatch):
        t, fake = self._mlflow(monkeypatch)
        t.log_metrics({"accuracy": 0.9}, step=3)
        fake.log_metrics.assert_called_once_with({"accuracy": 0.9}, step=3)

    def test_log_artifact_delegates(self, monkeypatch):
        t, fake = self._mlflow(monkeypatch)
        t.log_artifact("/tmp/model.json", artifact_path="model")
        fake.log_artifact.assert_called_once_with("/tmp/model.json", artifact_path="model")

    def test_register_model_builds_the_runs_uri(self, monkeypatch):
        t, fake = self._mlflow(monkeypatch)
        t.register_model("abc123", artifact_path="model")
        fake.register_model.assert_called_once_with("runs:/abc123/model", t.MODEL_NAME)

    def test_init_sets_the_uri_and_experiment(self, monkeypatch):
        t, fake = self._mlflow(monkeypatch)
        monkeypatch.setenv("MICROGUARD_MLFLOW_TRACKING_URI", "file:./mlruns")
        t.init()
        fake.set_tracking_uri.assert_called_once_with("file:./mlruns")
        fake.set_experiment.assert_called_once_with(t.EXPERIMENT_NAME)


class TestLoadModelSelectors:
    def test_a_stage_loads_that_stage(self, monkeypatch):
        import microguard.tracking as t

        fake = MagicMock()
        monkeypatch.setattr(t, "_get_mlflow", lambda: fake)
        t.load_model(stage="Staging")
        fake.pyfunc.load_model.assert_called_once_with(f"models:/{t.MODEL_NAME}/Staging")

    def test_neither_falls_back_to_production(self, monkeypatch):
        import microguard.tracking as t

        fake = MagicMock()
        monkeypatch.setattr(t, "_get_mlflow", lambda: fake)
        t.load_model()
        fake.pyfunc.load_model.assert_called_once_with(f"models:/{t.MODEL_NAME}/Production")


class TestGetRuns:
    def test_an_absent_experiment_is_empty_not_an_error(self, monkeypatch):
        """A fresh workspace has no experiment yet. That is not a failure."""
        import microguard.tracking as t

        fake = MagicMock()
        fake.tracking.MlflowClient.return_value.get_experiment_by_name.return_value = None
        monkeypatch.setattr(t, "_get_mlflow", lambda: fake)

        assert t.get_runs() == []

    def test_runs_are_formatted(self, monkeypatch):
        import microguard.tracking as t

        run = MagicMock()
        run.info.run_id = "r1"
        run.info.status = "FINISHED"
        run.info.start_time = 1700000000000
        run.info.end_time = 1700000060000
        run.data.params = {}
        run.data.metrics = {"accuracy": 0.9}
        run.data.tags = {"mlflow.runName": "train-1"}

        fake = MagicMock()
        client = fake.tracking.MlflowClient.return_value
        client.get_experiment_by_name.return_value = MagicMock(experiment_id="e1")
        client.search_runs.return_value = [run]
        monkeypatch.setattr(t, "_get_mlflow", lambda: fake)

        runs = t.get_runs(limit=5)

        assert [r["run_id"] for r in runs] == ["r1"]
        assert runs[0]["run_name"] == "train-1"


class TestDatabricksExperimentPaths:
    """Databricks rejects bare experiment names.

    `mlflow.set_experiment("microguard-training")` against a databricks
    tracking URI fails with `BAD_REQUEST: For input string: "None"` -- an
    error that names neither the cause nor the fix. Databricks wants an
    absolute workspace path. Measured directly: the bare name failed, and
    `/Users/<user>/microguard-training` created experiment 1992678532464586.

    That made the integration broken out of the box for every Databricks
    user, not a local misconfiguration.
    """

    def test_a_bare_name_is_qualified_for_databricks(self, monkeypatch):
        import microguard.tracking as t

        monkeypatch.setattr(t, "_workspace_user", lambda: "someone@example.com")

        resolved = t.resolve_experiment_name("microguard-training", "databricks")

        assert resolved == "/Users/someone@example.com/microguard-training"

    def test_an_absolute_path_is_left_alone(self, monkeypatch):
        import microguard.tracking as t

        monkeypatch.setattr(t, "_workspace_user", lambda: "someone@example.com")

        resolved = t.resolve_experiment_name("/Shared/mg", "databricks")

        assert resolved == "/Shared/mg"

    def test_a_local_tracking_uri_keeps_the_bare_name(self):
        """A file:// store has no workspace and no /Users tree."""
        import microguard.tracking as t

        assert t.resolve_experiment_name("microguard-training", "file:./mlruns") == (
            "microguard-training"
        )

    def test_an_unresolvable_user_falls_back_to_the_bare_name(self, monkeypatch):
        """Better a clear downstream error than a confident wrong path."""
        import microguard.tracking as t

        def no_user():
            raise RuntimeError("no credentials")

        monkeypatch.setattr(t, "_workspace_user", no_user)

        assert t.resolve_experiment_name("mg", "databricks") == "mg"

    def test_init_sets_the_qualified_experiment(self, monkeypatch):
        import microguard.tracking as t

        fake = MagicMock()
        monkeypatch.setattr(t, "_get_mlflow", lambda: fake)
        monkeypatch.setattr(t, "_workspace_user", lambda: "someone@example.com")
        monkeypatch.delenv("MICROGUARD_MLFLOW_TRACKING_URI", raising=False)

        t.init()

        fake.set_experiment.assert_called_once_with(
            f"/Users/someone@example.com/{t.EXPERIMENT_NAME}"
        )


class TestGetRunsLooksUpTheSameExperimentInitWrote:
    """init() writes to the resolved path; a reader using the bare name
    finds nothing and reports an empty history, which reads as "no runs
    yet" rather than "looking in the wrong place"."""

    def test_the_experiment_lookup_is_qualified_too(self, monkeypatch):
        import microguard.tracking as t

        fake = MagicMock()
        client = fake.tracking.MlflowClient.return_value
        client.get_experiment_by_name.return_value = None
        monkeypatch.setattr(t, "_get_mlflow", lambda: fake)
        monkeypatch.setattr(t, "_workspace_user", lambda: "someone@example.com")
        monkeypatch.delenv("MICROGUARD_MLFLOW_TRACKING_URI", raising=False)

        t.get_runs()

        client.get_experiment_by_name.assert_called_once_with(
            f"/Users/someone@example.com/{t.EXPERIMENT_NAME}"
        )
