"""`microguard dashboard` — serve the API and the built SPA on one port."""

from __future__ import annotations

import os

from .app import create_app

DEFAULT_PORT = 8500
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def run_dashboard(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    redis_url: str = "redis://localhost:6379",
    allow_config_writes: bool = False,
    token: str | None = None,
    deployment_id: str | None = None,
    feedback_dir: str | None = None,
) -> None:
    """Start the dashboard.

    Binds loopback by default. This UI reports every blocked visitor and, with
    --allow-config-writes, changes who gets blocked; it has no authentication,
    so putting it on a public interface is a decision an operator has to make
    deliberately.
    """
    import uvicorn

    app = create_app(
        redis_url=redis_url,
        allow_config_writes=allow_config_writes,
        static_dir=STATIC_DIR,
        token=token,
        deployment_id=deployment_id,
        feedback_dir=feedback_dir,
    )

    print(f"microguard dashboard on http://{host}:{port}")
    print(f"  redis: {redis_url}")
    print(f"  live feed: {'connected' if app.state.redis_connected else 'NOT CONNECTED'}")
    print(f"  config writes: {'enabled' if allow_config_writes else 'disabled'}")
    if not os.path.isdir(STATIC_DIR):
        print("  UI: NOT BUILT (run 'npm ci && npm run build' in gui/) - API only")
    print(f"  api token: {'required' if token else 'none'}")
    if host != "127.0.0.1" and not token:
        print("  WARNING: bound off loopback with no authentication")

    uvicorn.run(app, host=host, port=port, log_level="warning")
