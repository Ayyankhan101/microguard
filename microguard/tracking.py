"""MLflow tracking + Databricks Model Registry integration.

Lazy imports: mlflow is optional. All functions raise ImportError
with install instructions if mlflow is missing.

Usage:
    from microguard.tracking import init, start_run, log_params, log_metrics
    init()
    with start_run(run_name="my-run"):
        log_params({"epochs": 100})
        log_metrics({"accuracy": 0.95})
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

EXPERIMENT_NAME = os.getenv(
    "MICROGUARD_MLFLOW_EXPERIMENT", "microguard-training"
)
MODEL_NAME = os.getenv("MICROGUARD_MLFLOW_MODEL_NAME", "microguard")


def _get_mlflow():
    """Lazy import of mlflow."""
    try:
        import mlflow
        return mlflow
    except ImportError:
        raise ImportError(
            "MLflow is required for experiment tracking. Install with:\n"
            "  pip install 'microguard[mlflow]'\n"
            "Or directly: pip install 'mlflow>=2.10'"
        )


def resolved_tracking_uri(tracking_uri: str | None = None) -> str:
    """Where tracking will point, without importing mlflow to find out.

    Callers report this in failure messages. The default is "databricks",
    which needs DATABRICKS_HOST/DATABRICKS_TOKEN (or a configured profile)
    to resolve to anything — so a bare failure with no URI in it reads as
    "MLflow is broken" when the real answer is "no credentials".
    """
    if tracking_uri:
        return tracking_uri
    return os.environ.get("MICROGUARD_MLFLOW_TRACKING_URI", "databricks")


def _workspace_user() -> str:
    """The current Databricks user, for building their workspace path.

    Uses databricks-sdk, which mlflow already depends on for its Databricks
    integration, so this adds nothing to the dependency set.
    """
    from databricks.sdk import WorkspaceClient

    user = WorkspaceClient().current_user.me().user_name
    if not user:
        raise RuntimeError("Databricks returned no user_name")
    return user


def resolve_experiment_name(name: str, tracking_uri: str) -> str:
    """Qualify a bare experiment name for Databricks.

    Databricks stores experiments in the workspace file tree and rejects a
    bare name with `BAD_REQUEST: For input string: "None"` -- an error that
    names neither the cause nor the fix. Measured: "microguard-training"
    failed, "/Users/<user>/microguard-training" created the experiment. So
    the shipped default was broken for every Databricks user, not one
    misconfigured machine.

    Only for databricks URIs. A file:// store has no workspace and no /Users
    tree, and a bare name is correct there.

    If the user cannot be resolved, return the name unchanged: a clear
    downstream auth error beats a confidently wrong path.
    """
    if not tracking_uri.startswith("databricks"):
        return name
    if name.startswith("/"):
        return name
    try:
        return f"/Users/{_workspace_user()}/{name}"
    except Exception:
        logger.warning(
            "could not resolve the Databricks workspace user; using the bare "
            "experiment name %r, which Databricks will probably reject",
            name, exc_info=True,
        )
        return name


def init(tracking_uri: str | None = None) -> None:
    """Set tracking URI and experiment.

    Args:
        tracking_uri: MLflow tracking URI. Defaults to
            MICROGUARD_MLFLOW_TRACKING_URI env var, then "databricks".
    """
    mlflow = _get_mlflow()
    uri = resolved_tracking_uri(tracking_uri)
    experiment = resolve_experiment_name(EXPERIMENT_NAME, uri)
    logger.info("MLflow tracking URI: %s (experiment %s)", uri, experiment)
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(experiment)


def start_run(run_name: str | None = None):
    """Start MLflow run. Returns context manager.

    Usage:
        with start_run(run_name="train-v1") as run:
            log_params({"epochs": 100})
            # ... training ...
            log_metrics({"accuracy": 0.95})
    """
    mlflow = _get_mlflow()
    return mlflow.start_run(run_name=run_name)


def log_params(params: dict[str, Any]) -> None:
    """Log hyperparameters to active run."""
    mlflow = _get_mlflow()
    mlflow.log_params(params)


def log_metrics(metrics: dict[str, float], step: int | None = None) -> None:
    """Log metrics to active run, optionally at a step."""
    mlflow = _get_mlflow()
    mlflow.log_metrics(metrics, step=step)


def log_artifact(local_path: str, artifact_path: str | None = None) -> None:
    """Log a local file as run artifact."""
    mlflow = _get_mlflow()
    mlflow.log_artifact(local_path, artifact_path=artifact_path)


def register_model(
    run_id: str, artifact_path: str = "model"
) -> None:
    """Register model from run to Model Registry.

    Registers to Staging by default. Promote to Production via
    Databricks UI or MLflow API.
    """
    mlflow = _get_mlflow()
    model_uri = f"runs:/{run_id}/{artifact_path}"
    mlflow.register_model(model_uri, MODEL_NAME)


def load_model(
    version: int | None = None, stage: str | None = None
):
    """Load pyfunc model from Registry.

    Args:
        version: Specific model version number.
        stage: Model stage (e.g., "Staging", "Production").
            Defaults to "Production" if neither version nor stage given.

    Returns:
        MLflow pyfunc model with .predict() method.
    """
    mlflow = _get_mlflow()
    if version:
        return mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}/{version}")
    if stage:
        return mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}/{stage}")
    return mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}/Production")


def get_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch recent training runs for dashboard display.

    Returns list of dicts with keys: run_id, run_name, start_time,
    status, params, metrics.
    """
    mlflow = _get_mlflow()
    client = mlflow.tracking.MlflowClient()
    # The same qualification init() applies. Reading the bare name on
    # Databricks finds nothing and reports an empty history, which looks
    # like "no runs yet" rather than "looked in the wrong place".
    experiment = client.get_experiment_by_name(
        resolve_experiment_name(EXPERIMENT_NAME, resolved_tracking_uri())
    )
    if experiment is None:
        return []
    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=["start_time DESC"],
        max_results=limit,
    )
    return [_format_run(r) for r in runs]


def _format_run(run) -> dict[str, Any]:
    """Shape a run for JSON response."""
    return {
        "run_id": run.info.run_id,
        "run_name": run.data.tags.get("mlflow.runName"),
        "start_time": run.info.start_time,
        "status": run.info.status,
        "params": run.data.params,
        "metrics": run.data.metrics,
    }


class BotDetectorPyFunc:
    """MLflow pyfunc wrapper for BotDetector.

    Loads model weights + normalization from MLflow artifacts.
    Exposes detector via public attribute for direct access.

    Usage:
        model = mlflow.pyfunc.load_model("models:/microguard/Production")
        detector = model._model_impl.python_model.detector
        score = detector.predict(features)
    """

    def load_context(self, context):
        """Load model weights + normalization from artifacts.

        Expects artifacts["model"] to contain both model.json
        and normalization.json (logged together during training).
        """
        from .model import BotDetector

        model_dir = context.artifacts["model"]
        model_path = os.path.join(model_dir, "model.json")
        self.detector = BotDetector(model_path)

    def predict(self, context, model_input, params=None):
        """Score feature vectors.

        Args:
            model_input: pandas DataFrame, numpy array, or list of lists.
                Each row must have 19 features.

        Returns:
            numpy array of bot probabilities (0.0=human, 1.0=bot).
        """
        import numpy as np

        if hasattr(model_input, "values"):
            rows = model_input.values.tolist()
        elif isinstance(model_input, np.ndarray):
            rows = model_input.tolist()
        else:
            rows = list(model_input)

        return np.array(self.detector.predict_batch(rows))
