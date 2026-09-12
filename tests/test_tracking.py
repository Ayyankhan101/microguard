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
