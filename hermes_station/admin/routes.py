"""Admin routes.

Phase 0: login flow + /admin/api/status with the path block the compat test
asserts. Other endpoints return 501 until Phase 1 wires them up against real
provider/channel/gateway logic.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from hermes_station.admin.auth import (
    admin_auth_enabled,
    auth_state,
    clear_session_cookie,
    issue_session_cookie,
    require_admin,
    verify_password,
)
from hermes_station.config import (
    AdminSettings,
    Paths,
    extract_model_config,
    load_env_file,
    load_yaml_config,
)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _paths(request: Request) -> Paths:
    return request.app.state.paths


async def admin_index(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>hermes-station admin</title>"
        "<h1>hermes-station admin</h1>"
        "<p>Phase 0 skeleton. Real admin UI lands in Phase 1.</p>"
        '<p><a href="/admin/api/status">status JSON</a> · <form method=post action="/admin/logout" style="display:inline"><button>Log out</button></form></p>',
        status_code=200,
    )


async def admin_login_page(request: Request) -> Response:
    return _templates.TemplateResponse(
        request,
        "login.html",
        {"auth_enabled": admin_auth_enabled()},
    )


async def admin_login(request: Request) -> Response:
    form = await request.form()
    password = str(form.get("password") or "")
    if not verify_password(password):
        response = _templates.TemplateResponse(
            request,
            "login.html",
            {"auth_enabled": admin_auth_enabled(), "error": "Invalid password."},
            status_code=401,
        )
        return response
    response = RedirectResponse(url="/admin", status_code=302)
    issue_session_cookie(response)
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
            "phase": "0-skeleton",
        }
    )


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
        Route("/admin", admin_index, methods=["GET"]),
        Route("/admin/login", admin_login_page, methods=["GET"]),
        Route("/admin/login", admin_login, methods=["POST"]),
        Route("/admin/logout", admin_logout, methods=["POST"]),
        Route("/admin/api/status", api_status, methods=["GET"]),
        Route("/admin/api/provider/setup", _unimplemented, methods=["POST"]),
        Route("/admin/api/channels", _unimplemented, methods=["GET"]),
        Route("/admin/api/channels/save", _unimplemented, methods=["POST"]),
        Route("/admin/api/gateway/{action}", _unimplemented, methods=["POST"]),
        Route("/admin/api/webui/{action}", _unimplemented, methods=["POST"]),
        Route("/admin/api/pairing/{action}", _unimplemented, methods=["GET", "POST"]),
    ]
