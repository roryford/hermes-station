"""ASGI application factory for hermes-station.

Single-process model: control plane, hermes-webui (subprocess + HTTP proxy
at /), and the hermes-agent gateway (in-process asyncio task) share one
uvicorn event loop. The lifespan handler owns startup/shutdown of both
workloads. hermes-webui has to be a subprocess because it's hand-rolled on
stdlib http.server and can't be mounted as an ASGI sub-app.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hermes_station.admin.htmx_dashboard import routes as dashboard_routes
from hermes_station.admin.htmx_logs import routes as logs_routes
from hermes_station.admin.htmx_settings import routes as settings_routes
from hermes_station.admin.routes import admin_routes
from hermes_station.config import (
    AdminSettings,
    Paths,
    detect_provider_drift,
    load_env_file,
    load_yaml_config,
    normalize_config,
    seed_default_mcp_servers,
    seed_default_memory_provider,
    seed_env_file_to_os,
    seed_neutral_personality_default,
    seed_provider_from_env,
    seed_show_cost_default,
    write_yaml_config,
)
from hermes_station.gateway import Gateway, should_autostart
from hermes_station.health import routes as health_routes
from hermes_station.logs import attach_station_handler
from hermes_station.proxy import proxy_to_webui
from hermes_station.readiness import validate_readiness
from hermes_station.webui import WebUIProcess

logger = logging.getLogger("hermes_station.app")

_MAX_BODY_BYTES = 100 * 1024 * 1024  # 100 MB


class _SecurityHeadersMiddleware:
    """Inject security response headers on every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message.get("type") == "http.response.start":
                from starlette.datastructures import MutableHeaders

                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-XSS-Protection", "0")
                headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' https://unpkg.com 'unsafe-inline';"
                    " style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
                    " connect-src 'self'; frame-ancestors 'none';",
                )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class _BodySizeLimitMiddleware:
    """Reject requests with Content-Length exceeding the limit."""

    def __init__(self, app: ASGIApp, max_bytes: int = _MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            cl_raw = headers.get(b"content-length")
            if cl_raw is not None:
                try:
                    if int(cl_raw) > self.max_bytes:
                        from starlette.responses import Response

                        resp = Response("Request body too large", status_code=413)
                        await resp(scope, receive, send)
                        return
                except (ValueError, TypeError):
                    pass
        await self.app(scope, receive, send)


def _ensure_env_passthrough(paths: Paths, config: dict, keys: list[str]) -> None:
    """Add missing keys to terminal.env_passthrough and persist if changed."""
    terminal = config.setdefault("terminal", {})
    passthrough = terminal.setdefault("env_passthrough", [])
    if not isinstance(passthrough, list):
        # normalize_config should have fixed this already; warn loudly if not.
        logger.warning(
            "terminal.env_passthrough is %s, expected list — replacing with empty list",
            type(passthrough).__name__,
        )
        passthrough = []
        terminal["env_passthrough"] = passthrough
    additions = [k for k in keys if k not in passthrough]
    if additions:
        passthrough.extend(additions)
        write_yaml_config(paths.config_path, config)


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    paths: Paths = app.state.paths
    paths.ensure()

    app.state.proxy_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=10.0)
    )

    webui: WebUIProcess = app.state.webui
    gateway: Gateway = app.state.gateway

    try:
        settings = AdminSettings()
        if not settings.admin_password and settings.webui_password:
            logger.warning(
                "HERMES_ADMIN_PASSWORD is unset — falling back to HERMES_WEBUI_PASSWORD "
                "for admin auth. Set HERMES_ADMIN_PASSWORD to use a dedicated credential."
            )
        # First-boot only seeds (all no-clobber — any existing value wins).
        if seed_default_memory_provider(paths.config_path):
            logger.info("seeded default memory provider: holographic")
        added_mcp = seed_default_mcp_servers(paths.config_path)
        if added_mcp:
            logger.info("seeded default MCP servers (disabled): %s", ", ".join(added_mcp))
        if seed_neutral_personality_default(paths.config_path):
            logger.info("seeded default personality: default")
        if seed_show_cost_default(paths.config_path):
            logger.info("seeded default show_cost: true")
        seeded_provider = seed_provider_from_env(paths.config_path, dict(os.environ))
        if seeded_provider:
            public_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
            settings_link = f"https://{public_url}/admin/settings" if public_url else "/admin/settings"
            logger.info("provider auto-seeded — visit %s to verify", settings_link)
        config = load_yaml_config(paths.config_path)
        config, norm_changes = normalize_config(config)
        if norm_changes:
            write_yaml_config(paths.config_path, config)
            for change in norm_changes:
                logger.warning("config normalized on load: %s", change)
        _ensure_env_passthrough(paths, config, ["GITHUB_TOKEN", "GH_TOKEN"])
        env_values = load_env_file(paths.env_path)
        # CONTRACT.md §2.1: .env values take precedence over process env.
        # The gateway runs in-process and reads os.environ directly — seed here
        # so provider credentials stored via the admin UI override Railway env vars.
        seed_env_file_to_os(paths.env_path)
        # Boot validator: reconcile intended vs actual readiness. The result
        # is cached on app.state for /health to consume; we never abort on
        # missing capability — image is publicly shareable, default posture
        # is warn-and-continue.
        try:
            app.state.readiness = validate_readiness(paths, config, env_values)
        except Exception:  # noqa: BLE001
            logger.exception("readiness validation raised; continuing")
            app.state.readiness = None
        drift_msgs = detect_provider_drift(config, dict(os.environ))
        for drift_msg in drift_msgs:
            logger.warning("%s", drift_msg)
        # Surface drift in the configured provider's readiness row so /health
        # callers see it without scraping logs. Drift is always tied to the
        # one configured provider, so we attach to provider:<name>.
        if drift_msgs and app.state.readiness is not None:
            model_block = config.get("model")
            if isinstance(model_block, dict):
                provider = str(model_block.get("provider") or "").strip().lower()
                row_key = f"provider:{provider}" if provider else ""
                row = app.state.readiness.readiness.get(row_key) if row_key else None
                if row is not None:
                    row.notes = "; ".join(drift_msgs)
        if should_autostart(mode=settings.gateway_autostart, config=config, env_values=env_values):
            logger.info(
                "autostarting gateway (mode=%s, provider configured + channel present)",
                settings.gateway_autostart,
            )
            await gateway.start()
        else:
            logger.info("gateway not autostarting (mode=%s)", settings.gateway_autostart)
    except Exception:  # noqa: BLE001
        logger.exception("gateway autostart check failed")

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
        webui.mark_disabled()
        logger.warning("hermes-webui source not found at %s; subprocess will not start", paths.webui_src)

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


_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def create_app() -> Starlette:
    attach_station_handler()
    paths = Paths()
    base_dir = Path(__file__).resolve().parent

    routes = [
        *health_routes(),
        *dashboard_routes(),
        *settings_routes(),
        *logs_routes(),
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
    # Filled in by lifespan after config load. Set a default so /health
    # responds gracefully if a probe lands before lifespan completes.
    app.state.readiness = None
    app.add_middleware(_SecurityHeadersMiddleware)
    app.add_middleware(_BodySizeLimitMiddleware)
    return app


app = create_app()
