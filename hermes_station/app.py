"""ASGI application factory for hermes-station.

Single-process model: control plane, hermes-webui (subprocess + HTTP proxy
at /), and the hermes-agent gateway (in-process asyncio task) share one
uvicorn event loop. The lifespan handler owns startup/shutdown of both
workloads. hermes-webui has to be a subprocess because it's hand-rolled on
stdlib http.server and can't be mounted as an ASGI sub-app.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from hermes_station.admin.htmx_dashboard import routes as dashboard_routes
from hermes_station.admin.htmx_settings import routes as settings_routes
from hermes_station.admin.routes import admin_routes
from hermes_station.config import AdminSettings, Paths, load_env_file, load_yaml_config
from hermes_station.gateway import Gateway, should_autostart
from hermes_station.proxy import proxy_to_webui
from hermes_station.webui import WebUIProcess

logger = logging.getLogger("hermes_station.app")


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    paths: Paths = app.state.paths
    paths.ensure()

    app.state.proxy_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    webui: WebUIProcess = app.state.webui
    gateway: Gateway = app.state.gateway

    server_py = paths.webui_src / "server.py"
    if server_py.exists():
        try:
            await webui.start()
            if not await webui.wait_ready():
                logger.warning(
                    "hermes-webui not healthy within %.0fs; supervisor will keep trying",
                    WebUIProcess.STARTUP_GRACE_SECONDS,
                )
        except Exception:  # noqa: BLE001
            logger.exception("hermes-webui startup raised; supervisor will keep trying")
    else:
        logger.warning(
            "hermes-webui source not found at %s; subprocess will not start", paths.webui_src
        )

    try:
        settings = AdminSettings()
        config = load_yaml_config(paths.config_path)
        env_values = load_env_file(paths.env_path)
        if should_autostart(
            mode=settings.gateway_autostart, config=config, env_values=env_values
        ):
            logger.info(
                "autostarting gateway (mode=%s, provider configured + channel present)",
                settings.gateway_autostart,
            )
            await gateway.start()
        else:
            logger.info(
                "gateway not autostarting (mode=%s)", settings.gateway_autostart
            )
    except Exception:  # noqa: BLE001
        logger.exception("gateway autostart check failed")

    try:
        yield
    finally:
        logger.info("lifespan shutdown: stopping supervisors")
        with suppress(Exception):
            await gateway.stop()
        with suppress(Exception):
            await webui.stop()
        with suppress(Exception):
            await app.state.proxy_client.aclose()


async def health(request) -> PlainTextResponse:  # noqa: ARG001
    return PlainTextResponse("ok")


_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def create_app() -> Starlette:
    paths = Paths()
    base_dir = Path(__file__).resolve().parent

    routes = [
        Route("/health", health),
        *dashboard_routes(),
        *settings_routes(),
        *admin_routes(),
        Mount(
            "/admin/static",
            app=StaticFiles(directory=str(base_dir / "static"), check_dir=False),
            name="admin-static",
        ),
        Route("/", proxy_to_webui, methods=_PROXY_METHODS),
        Route("/{path:path}", proxy_to_webui, methods=_PROXY_METHODS),
    ]

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.paths = paths
    app.state.webui = WebUIProcess(
        webui_src=paths.webui_src,
        hermes_home=paths.hermes_home,
        webui_state_dir=paths.webui_state_dir,
        workspace_dir=paths.workspace_dir,
        config_path=paths.config_path,
    )
    app.state.gateway = Gateway(hermes_home=paths.hermes_home)
    return app


app = create_app()
