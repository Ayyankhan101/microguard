"""MLflow runs endpoint — training run history for dashboard display."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/mlflow", tags=["mlflow"])


@router.get("/runs")
def mlflow_runs(limit: int = 20) -> dict:
    """Recent training runs from MLflow.

    Returns list of runs with params, metrics, and status.
    Requires MLflow to be installed and configured.
    """
    try:
        from ..tracking import get_runs
        return {"runs": get_runs(limit)}
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="MLflow not installed. Install with: pip install 'microguard[mlflow]'",
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"MLflow unavailable: {e}") from e
