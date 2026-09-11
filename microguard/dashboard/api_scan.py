"""Scan endpoints — a thin HTTP shell over cli.scan_logfile()."""

from __future__ import annotations

import os
import tempfile
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..cli import DEFAULT_THRESHOLD, scan_logfile
from ..model import DEFAULT_MODEL_PATH
from ..report import (
    format_cloudflare_rule,
    format_html,
    format_json,
    format_nginx_denylist,
)
from .paths import DATA_DIR, resolve_within

router = APIRouter(prefix="/api/scan", tags=["scan"])

SAMPLE_SUFFIX = ".log"


class ScanRequest(BaseModel):
    """A scan of one bundled sample log.

    Extra keys are rejected rather than ignored: a typo'd "treshold" that
    silently scanned at the default would be invisible in the UI.
    """

    model_config = {"extra": "forbid"}

    sample: str
    format: str = "auto"
    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0.0, le=1.0)
    timeout_minutes: int = Field(default=30, gt=0)


def _count_lines(path: str) -> int:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


@router.get("/samples")
def list_samples() -> dict:
    """The log files bundled with the package, offered as one-click scans."""
    if not os.path.isdir(DATA_DIR):
        return {"samples": []}
    samples = []
    for name in sorted(os.listdir(DATA_DIR)):
        path = os.path.join(DATA_DIR, name)
        if not name.endswith(SAMPLE_SUFFIX) or not os.path.isfile(path):
            continue
        samples.append(
            {
                "name": name,
                "bytes": os.path.getsize(path),
                "lines": _count_lines(path),
            }
        )
    return {"samples": samples}


def _resolve_sample(name: str) -> str:
    path = resolve_within(DATA_DIR, name)
    if path is None or not name.endswith(SAMPLE_SUFFIX) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"No such sample: {name}")
    return path


@router.post("")
def scan(request: ScanRequest) -> dict:
    """Scan a bundled sample and return scan_logfile()'s dict verbatim."""
    return scan_logfile(
        _resolve_sample(request.sample),
        fmt=request.format,
        threshold=request.threshold,
        model_path=DEFAULT_MODEL_PATH,
        timeout_minutes=request.timeout_minutes,
    )


MAX_UPLOAD_BYTES = 64 * 1024 * 1024
_UPLOAD_CHUNK = 1024 * 1024


@router.post("/upload")
async def scan_upload(
    file: Annotated[UploadFile, File()],
    format: Annotated[str, Form()] = "auto",
    threshold: Annotated[float, Form()] = DEFAULT_THRESHOLD,
    timeout_minutes: Annotated[int, Form()] = 30,
) -> dict:
    """Scan an uploaded log file.

    The upload is streamed to a temp file with a hard cap and deleted in a
    finally, so a browser cannot fill the host's disk and a scan failure cannot
    leave the log lying around.
    """
    descriptor, path = tempfile.mkstemp(suffix=".log")
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            while chunk := await file.read(_UPLOAD_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds {MAX_UPLOAD_BYTES} bytes",
                    )
                handle.write(chunk)
        return scan_logfile(
            path,
            fmt=format,
            threshold=threshold,
            model_path=DEFAULT_MODEL_PATH,
            timeout_minutes=timeout_minutes,
        )
    finally:
        os.unlink(path)


_EXPORTERS = {
    "json": format_json,
    "html": format_html,
    "nginx": format_nginx_denylist,
    "cloudflare": format_cloudflare_rule,
}

_EXPORT_SUFFIX = {"json": "json", "html": "html", "nginx": "conf", "cloudflare": "txt"}


class ExportRequest(BaseModel):
    model_config = {"extra": "forbid"}

    results: dict
    format: Literal["json", "html", "nginx", "cloudflare"]


@router.post("/export")
def export(request: ExportRequest) -> PlainTextResponse:
    """Render a scan result through report.py's existing formatters.

    Always text/plain with an attachment disposition, never text/html:
    format_html() interpolates attacker-controlled user agents and URLs without
    escaping (report.py:455), so serving it inline would be stored XSS on the
    dashboard's own origin.
    """
    body = _EXPORTERS[request.format](request.results)
    filename = f"microguard-report.{_EXPORT_SUFFIX[request.format]}"
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
