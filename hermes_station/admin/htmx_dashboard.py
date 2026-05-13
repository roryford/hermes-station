"""HTMX-driven admin dashboard page.

Renders `/admin` as a single-screen status view and exposes a fragment endpoint
(`/admin/_partial/status`) that the page polls every 5 seconds to refresh the
WebUI/Gateway/Model/Channels panels in place. The supervisor control buttons
POST to the existing JSON action endpoints in `hermes_station.admin.routes`
(`/admin/api/{gateway,webui}/{start,stop,restart}`) and target the same
fragment for the response swap.

The dashboard is intentionally read-mostly: provider configuration and channel
secrets are edited on the Settings page; pending pairings are managed on the
Pairings page. The summary card here links to both.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import is_authenticated, require_admin
from hermes_station.admin.channels import CHANNEL_CATALOG, channel_status
from hermes_station.admin.mcp import load_mcp_status, toggle_mcp_server
from hermes_station.admin.pairing import get_pending
from hermes_station.admin.provider import PROVIDER_CATALOG
from hermes_station.config import (
    AdminSettings,
    Paths,
    extract_model_config,
    load_env_file,
    load_yaml_config,
)

logger = logging.getLogger(__name__)


def _paths(request: Request) -> Paths:
    return request.app.state.paths


async def _gather_status(request: Request) -> dict[str, Any]:
    """Build the dict consumed by `_status_panel.html`.

    Mirrors the data shape of `api_status` in `routes.py` so the template can
    rely on the same keys whether it was rendered from the page or refreshed
    from the fragment endpoint.
    """
    paths = _paths(request)
    settings = AdminSettings()
    config = load_yaml_config(paths.config_path)
    env_values = load_env_file(paths.env_path)
    model = extract_model_config(config)

    webui = getattr(request.app.state, "webui", None)
    gateway = getattr(request.app.state, "gateway", None)

    webui_running = bool(webui and webui.is_running())
    webui_healthy = bool(webui and await webui.is_healthy()) if webui else False
    gateway_running = bool(gateway and gateway.is_running())
    gateway_healthy = bool(gateway and gateway.is_healthy()) if gateway else False
    gateway_state = gateway.gateway_state if gateway else "unknown"

    webui_block = {
        "running": webui_running,
        "healthy": webui_healthy,
        "badge": _supervisor_badge(running=webui_running, healthy=webui_healthy),
    }
    gateway_block = {
        "running": gateway_running,
        "healthy": gateway_healthy,
        "state": gateway_state,
        "badge": _gateway_badge(running=gateway_running, state=gateway_state),
    }

    provider_meta = PROVIDER_CATALOG.get((model.provider or "").lower())
    provider_block = {
        "configured": bool(model.provider),
        "id": model.provider,
        "label": provider_meta["label"] if provider_meta else model.provider,
        "default": model.default,
        "base_url": model.base_url,
    }

    channels = channel_status(env_values)
    pending_count = len(get_pending(paths.pairing_dir))

    memory_raw = config.get("memory") if isinstance(config.get("memory"), dict) else {}
    memory_provider = (memory_raw.get("provider") or "").strip()
    memory_block = {
        "provider": memory_provider or "built-in only",
        "configured": bool(memory_provider),
    }

    return {
        "paths": {
            "hermes_home": str(paths.hermes_home),
            "config_path": str(paths.config_path),
            "env_path": str(paths.env_path),
            "webui_state_dir": str(paths.webui_state_dir),
            "workspace_dir": str(paths.workspace_dir),
        },
        "autostart_mode": settings.gateway_autostart,
        "webui": webui_block,
        "gateway": gateway_block,
        "provider": provider_block,
        "memory": memory_block,
        "channels": channels,
        "channel_catalog": CHANNEL_CATALOG,
        "pending_pairings": pending_count,
    }


def _supervisor_badge(*, running: bool, healthy: bool) -> dict[str, str]:
    if running and healthy:
        return {"tone": "success", "label": "Healthy"}
    if running and not healthy:
        return {"tone": "warning", "label": "Starting"}
    return {"tone": "muted", "label": "Stopped"}


def _gateway_badge(*, running: bool, state: str) -> dict[str, str]:
    # Map `gateway_state.json` strings (CONTRACT.md §4) to a UI tone.
    if state == "running":
        return {"tone": "success", "label": "Running"}
    if state in {"starting", "stopping"}:
        return {"tone": "warning", "label": state.capitalize()}
    if state == "startup_failed":
        return {"tone": "danger", "label": "Startup failed"}
    if running:
        return {"tone": "warning", "label": "Starting"}
    return {"tone": "muted", "label": "Stopped"}


def _mcp_context(request: Request) -> dict[str, Any]:
    paths = _paths(request)
    return {"mcp_servers": load_mcp_status(paths.config_path, paths.env_path)}


async def dashboard_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    context: dict[str, Any] = {
        "active": "dashboard",
        "title": "Dashboard",
    }
    context.update(_mcp_context(request))
    return _templates.TemplateResponse(request, "admin/dashboard.html", context)


async def mcp_fragment_toggle(request: Request) -> Response:
    """Toggle one MCP server's enabled flag. Restarts the gateway so
    hermes-agent re-reads `mcp_servers` from config.yaml on next start."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    form = await request.form()
    name = str(form.get("name") or "").strip()
    alert: dict[str, str]
    try:
        new_value = toggle_mcp_server(paths.config_path, name)
        verb = "enabled" if new_value else "disabled"
        gateway = getattr(request.app.state, "gateway", None)
        if gateway is not None:
            await gateway.restart()
        alert = {"kind": "success", "message": f"MCP server '{name}' {verb}."}
    except ValueError as exc:
        logger.warning("MCP toggle error: %s", exc)
        alert = {"kind": "error", "message": "Operation failed — check logs for details."}
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP toggle unexpected error: %s", exc)
        alert = {"kind": "error", "message": "Operation failed — check logs for details."}
    context: dict[str, Any] = {"alert": alert}
    context.update(_mcp_context(request))
    return _templates.TemplateResponse(request, "admin/_mcp_card.html", context)


async def status_fragment(request: Request) -> Response:
    # Fragment endpoints are HTMX-targeted: returning a 302 redirect would
    # silently swap the login page into the dashboard panel. Return 401 so
    # HTMX's response handler / our tests can detect the failure cleanly.
    if not is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    context = await _gather_status(request)
    return _templates.TemplateResponse(
        request,
        "admin/_status_panel.html",
        context,
    )


def routes() -> list[Route]:
    return [
        Route("/admin", dashboard_page, methods=["GET"]),
        Route("/admin/_partial/status", status_fragment, methods=["GET"]),
        Route("/admin/_partial/mcp/toggle", mcp_fragment_toggle, methods=["POST"]),
    ]
