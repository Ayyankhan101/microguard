"""Databricks Apps entry point for the Microguard dashboard.

This previously imported `microguard.dashboard.app:asgi_app`, which does not
exist -- the factory is `create_app`. The deploy would have failed at import,
so this file had never run.

Databricks Apps is not localhost. `microguard dashboard` binds loopback for a
reason: the UI reports every blocked visitor and, with config writes enabled,
changes who gets blocked. Here it is reachable by anyone the workspace lets in,
so the shared-secret gate is mandatory rather than optional, and config writes
stay off.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from microguard.dashboard.app import create_app
from microguard.dashboard.server import STATIC_DIR

_TOKEN = os.environ.get("MICROGUARD_API_TOKEN")
if not _TOKEN:
    raise RuntimeError(
        "MICROGUARD_API_TOKEN is not set. The dashboard has no authentication "
        "of its own, and on Databricks Apps it is not behind loopback. Set the "
        "secret in the app's environment before deploying."
    )

# Databricks Apps expects a module-level ASGI app named `app`.
app = create_app(
    redis_url=os.environ.get("MICROGUARD_REDIS_URL") or None,
    # Deliberately not configurable from the environment: PUT /api/live/config
    # changes the live block threshold, and nothing here authenticates *which*
    # workspace user is asking.
    allow_config_writes=False,
    static_dir=STATIC_DIR,
    token=_TOKEN,
    deployment_id=os.environ.get("MICROGUARD_DEPLOYMENT_ID"),
    feedback_dir=os.environ.get("MICROGUARD_FEEDBACK_DIR"),
)
