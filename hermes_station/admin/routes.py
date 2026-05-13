"""Admin routes."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import (
    admin_auth_enabled,
    auth_state,
    clear_session_cookie,
    issue_session_cookie,
    require_admin,
    verify_password,
)
from hermes_station.admin.channels import channel_status, save_channel_values
from hermes_station.admin.pairing import approve, deny, get_approved, get_pending, revoke
from hermes_station.admin.provider import apply_provider_setup
from hermes_station.config import (
    AdminSettings,
    Paths,
    extract_model_config,
    load_env_file,
    load_yaml_config,
    seed_env_file_to_os,
)
from hermes_station.gateway import Gateway

logger = logging.getLogger(__name__)

_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 60.0


def _paths(request: Request) -> Paths:
    return request.app.state.paths


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def admin_login_page(request: Request) -> Response:
    return _templates.TemplateResponse(
        request,
        "login.html",
        {"auth_enabled": admin_auth_enabled()},
    )


async def admin_login(request: Request) -> Response:
    # Rate-limit by client IP to slow brute-force attacks.
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in _login_attempts[client_ip] if now - t < _LOGIN_WINDOW_SECONDS]
    if len(recent) >= _LOGIN_MAX_ATTEMPTS:
        return Response("Too many login attempts. Try again later.", status_code=429)
    _login_attempts[client_ip] = recent

    form = await request.form()
    password = str(form.get("password") or "")
    if not verify_password(password):
        _login_attempts[client_ip].append(now)
        response = _templates.TemplateResponse(
            request,
            "login.html",
            {"auth_enabled": admin_auth_enabled(), "error": "Invalid password."},
            status_code=401,
        )
        return response
    response = RedirectResponse(url="/admin", status_code=302)
    issue_session_cookie(response, request)
    return response


async def admin_logout(request: Request) -> Response:
    response = RedirectResponse(url="/admin/login", status_code=302)
    clear_session_cookie(response)
    return response


async def api_status(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard

    paths = _paths(request)
    settings = AdminSettings()
    config = load_yaml_config(paths.config_path)
    env_values = load_env_file(paths.env_path)
    model = extract_model_config(config)

    webui = getattr(request.app.state, "webui", None)
    gateway = getattr(request.app.state, "gateway", None)
    webui_status = {
        "running": bool(webui and webui.is_running()),
        "healthy": bool(webui and await webui.is_healthy()) if webui else False,
    }
    gateway_status = {
        "running": bool(gateway and gateway.is_running()),
        "healthy": bool(gateway and gateway.is_healthy()) if gateway else False,
        "state": gateway.gateway_state if gateway else "unknown",
    }

    # The `paths` block is part of the data contract — see CONTRACT.md §5.1.
    return JSONResponse(
        {
            "paths": {
                "hermes_home": str(paths.hermes_home),
                "config_path": str(paths.config_path),
                "env_path": str(paths.env_path),
                "webui_state_dir": str(paths.webui_state_dir),
                "workspace_dir": str(paths.workspace_dir),
            },
            "model": {
                "provider": model.provider,
                "default": model.default,
                "base_url": model.base_url,
            },
            "env_keys_present": sorted(env_values.keys()),
            "autostart_mode": settings.gateway_autostart,
            "auth": {
                "enabled": auth_state(request).enabled,
                "authenticated": True,
            },
            "webui": webui_status,
            "gateway": gateway_status,
            "phase": "1",
        }
    )


async def api_provider_setup(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    body = await _json_body(request)
    try:
        result = apply_provider_setup(
            config_path=paths.config_path,
            env_path=paths.env_path,
            provider=str(body.get("provider") or ""),
            model=str(body.get("model") or ""),
            api_key=str(body.get("api_key") or ""),
            base_url=str(body.get("base_url") or ""),
        )
    except ValueError as exc:
        logger.warning("provider setup error: %s", exc)
        return JSONResponse({"ok": False, "error": "Invalid configuration — check logs for details."}, status_code=400)
    seed_env_file_to_os(paths.env_path)
    gateway: Gateway = request.app.state.gateway
    await gateway.restart()
    return JSONResponse({"ok": True, "result": result})


async def api_channels_get(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    env_values = load_env_file(paths.env_path)
    return JSONResponse({"channels": channel_status(env_values)})


async def api_channels_save(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    body = await _json_body(request)
    # Body values are expected to be `str | None`; coerce everything else to a string.
    updates: dict[str, str | None] = {}
    for key, value in body.items():
        if value is None:
            updates[key] = None
        else:
            updates[key] = str(value)
    try:
        env_values = save_channel_values(paths.env_path, updates)
    except ValueError as exc:
        logger.warning("channel save error: %s", exc)
        return JSONResponse({"ok": False, "error": "Invalid configuration — check logs for details."}, status_code=400)
    gateway: Gateway = request.app.state.gateway
    await gateway.restart()
    return JSONResponse({"ok": True, "channels": channel_status(env_values)})


async def api_pairing_pending(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    return JSONResponse({"pending": get_pending(paths.pairing_dir)})


async def api_pairing_approved(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    return JSONResponse({"approved": get_approved(paths.pairing_dir)})


async def api_pairing_action(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    action = request.path_params["action"]
    if action == "pending":
        return await api_pairing_pending(request)
    if action == "approved":
        return await api_pairing_approved(request)
    if action not in {"approve", "deny", "revoke"}:
        return JSONResponse({"ok": False, "error": f"unknown action: {action}"}, status_code=400)
    paths = _paths(request)
    body = await _json_body(request)
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"ok": False, "error": "user_id is required"}, status_code=400)
    try:
        if action == "approve":
            approve(paths.pairing_dir, user_id)
        elif action == "deny":
            deny(paths.pairing_dir, user_id)
        else:
            revoke(paths.pairing_dir, user_id)
    except KeyError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True})


_SUPERVISOR_ACTIONS = frozenset({"start", "stop", "restart"})


async def _supervisor_action(request: Request, supervisor_attr: str) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    action = request.path_params.get("action", "")
    if action not in _SUPERVISOR_ACTIONS:
        return JSONResponse({"ok": False, "error": f"unknown action: {action}"}, status_code=400)
    supervisor = getattr(request.app.state, supervisor_attr, None)
    if supervisor is None:
        return JSONResponse(
            {"ok": False, "error": f"{supervisor_attr} supervisor not initialized"},
            status_code=503,
        )
    try:
        if action == "start":
            await supervisor.start()
        elif action == "stop":
            await supervisor.stop()
        else:
            await supervisor.restart()
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor action error (%s %s): %s", supervisor_attr, action, exc)
        return JSONResponse({"ok": False, "error": "Action failed — check logs for details."}, status_code=500)
    return JSONResponse({"ok": True, "action": action})


async def api_gateway_action(request: Request) -> Response:
    return await _supervisor_action(request, "gateway")


async def api_webui_action(request: Request) -> Response:
    return await _supervisor_action(request, "webui")


async def _unimplemented(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    return JSONResponse(
        {"error": "not_implemented", "phase": "0-skeleton", "endpoint": request.url.path},
        status_code=501,
    )


def admin_routes() -> list[Route]:
    return [
        Route("/admin/login", admin_login_page, methods=["GET"]),
        Route("/admin/login", admin_login, methods=["POST"]),
        Route("/admin/logout", admin_logout, methods=["POST"]),
        Route("/admin/api/status", api_status, methods=["GET"]),
        Route("/admin/api/provider/setup", api_provider_setup, methods=["POST"]),
        Route("/admin/api/channels", api_channels_get, methods=["GET"]),
        Route("/admin/api/channels/save", api_channels_save, methods=["POST"]),
        Route("/admin/api/pairing/pending", api_pairing_pending, methods=["GET"]),
        Route("/admin/api/pairing/approved", api_pairing_approved, methods=["GET"]),
        Route("/admin/api/pairing/{action}", api_pairing_action, methods=["GET", "POST"]),
        Route("/admin/api/gateway/{action}", api_gateway_action, methods=["POST"]),
        Route("/admin/api/webui/{action}", api_webui_action, methods=["POST"]),
    ]
