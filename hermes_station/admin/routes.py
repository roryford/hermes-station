"""Admin routes."""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any
from urllib.parse import urlsplit

import hermes_station

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import (
    admin_auth_enabled,
    auth_state,
    clear_session_cookie,
    is_authenticated,
    issue_session_cookie,
    require_admin,
    verify_password,
)
from hermes_station.admin.bridge_auth import verify_webui_session
from hermes_station.admin.channels import channel_status, save_channel_values
from hermes_station.admin.pairing import approve, deny, get_approved, get_pending, revoke
from hermes_station.admin.provider import apply_provider_setup
from hermes_station.config import (
    AdminSettings,
    Paths,
    extract_model_config,
    load_env_file,
    load_yaml_config,
    pilot_admin_extension_enabled,
    seed_env_file_to_os,
)
from hermes_station.gateway import Gateway

logger = logging.getLogger(__name__)

_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_IPS = 10_000


def _prune_login_attempts() -> None:
    """Evict IPs outside the rate-limit window; cap total entries to prevent unbounded growth.

    When the dict exceeds _LOGIN_MAX_IPS entries, the oldest (by most-recent attempt)
    are evicted down to 75% of the cap before the time-window prune runs.
    """
    if len(_login_attempts) > _LOGIN_MAX_IPS:
        target = int(_LOGIN_MAX_IPS * 0.75)
        sorted_ips = sorted(
            _login_attempts.keys(),
            key=lambda ip: max(_login_attempts[ip]) if _login_attempts[ip] else 0.0,
        )
        for ip in sorted_ips[: len(_login_attempts) - target]:
            _login_attempts.pop(ip, None)
    now = time.time()
    stale = [
        ip
        for ip, times in list(_login_attempts.items())
        if not any(now - t < _LOGIN_WINDOW_SECONDS for t in times)
    ]
    for ip in stale:
        _login_attempts.pop(ip, None)


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
    _prune_login_attempts()
    # Rate-limit by client IP to slow brute-force attacks.
    xff = request.headers.get("x-forwarded-for", "")
    client_ip = xff.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    now = time.time()
    recent = [t for t in _login_attempts[client_ip] if now - t < _LOGIN_WINDOW_SECONDS]
    if len(recent) >= _LOGIN_MAX_ATTEMPTS:
        return Response("Too many login attempts. Try again later.", status_code=429)
    _login_attempts[client_ip] = recent

    form = await request.form()
    password = str(form.get("password") or "")
    if not verify_password(password):
        _login_attempts[client_ip].append(now)
        return _templates.TemplateResponse(
            request,
            "login.html",
            {"auth_enabled": admin_auth_enabled(), "error": "Invalid password."},
            status_code=401,
        )
    redirect = RedirectResponse(url="/admin", status_code=302)
    issue_session_cookie(redirect, request)
    return redirect


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
    # NOTE: config_path and env_path point directly at the secrets files on disk.
    # This is admin-only (require_admin guard above), but represents an information
    # disclosure risk if an admin session is hijacked. Kept for contract stability.
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
            "env_keys_present": bool(env_values),
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


async def api_ping(request: Request) -> Response:
    """Dual-cookie diagnostic ping.

    Accepts EITHER a webui ``hermes_session`` cookie (verified via the bridge
    loopback to webui's ``/api/auth/status``) OR the legacy
    ``hermes_station_admin`` cookie. Returns ``{ok: true, via: ...}`` so
    operators can confirm which auth path is healthy during the pilot
    transition.

    Does NOT use ``require_admin()`` — it implements its own dual-cookie logic.
    """
    if await verify_webui_session(request):
        return JSONResponse({"ok": True, "via": "webui_session"})
    if is_authenticated(request):
        return JSONResponse({"ok": True, "via": "station_admin"})
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _pilot_compose_gateway(request: Request) -> dict[str, Any]:
    """Best-effort gateway snapshot for the pilot status payload.

    Returns nullable fields so a mid-write ``gateway_state.json`` (rewritten by
    hermes-agent) can't 500 the whole response. Composed from the live
    ``Gateway`` supervisor on ``app.state`` when present.
    """
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        return {"state": "unknown", "pid": None, "uptime_s": None, "platform": None, "connection": None}
    snap = gateway.snapshot()
    raw_state = gateway.read_state()
    pid = raw_state.get("pid") if isinstance(raw_state.get("pid"), int) else None
    uptime = raw_state.get("uptime_s") if isinstance(raw_state.get("uptime_s"), int) else None
    return {
        "state": str(snap.get("state") or "unknown"),
        "pid": pid,
        "uptime_s": uptime,
        "platform": snap.get("platform"),
        "connection": snap.get("connection"),
    }


def _pilot_compose_webui(request: Request) -> dict[str, Any]:
    webui = getattr(request.app.state, "webui", None)
    if webui is None:
        return {"state": "unknown", "pid": None}
    snap = webui.snapshot()
    pid_value = snap.get("pid")
    pid = pid_value if isinstance(pid_value, int) else None
    return {"state": str(snap.get("state") or "unknown"), "pid": pid}


def _pilot_compose_provider(request: Request) -> dict[str, Any]:
    paths = _paths(request)
    config = load_yaml_config(paths.config_path)
    model = extract_model_config(config)
    return {
        "name": model.provider or None,
        "model": model.default or None,
    }


def _pilot_compose_channels(request: Request) -> list[dict[str, Any]]:
    """Channel rows from the cached readiness report.

    A "channel" row is either the bare ``discord`` key or anything namespaced
    under ``channel:<slug>`` (matches ``readiness.validate_readiness``).
    """
    readiness = getattr(request.app.state, "readiness", None)
    if readiness is None:
        return []
    rows = []
    for key, row in readiness.readiness.items():
        if key == "discord":
            name = "discord"
        elif key.startswith("channel:"):
            name = key.split(":", 1)[1]
        else:
            continue
        rows.append(
            {
                "name": name,
                "intended": bool(row.intended),
                "ready": bool(row.ready),
                "reason": row.reason or None,
            }
        )
    return rows


def _pilot_compose_memory(request: Request) -> dict[str, Any]:
    paths = _paths(request)
    config = load_yaml_config(paths.config_path)
    raw_memory = config.get("memory")
    memory_block: dict[str, Any] = raw_memory if isinstance(raw_memory, dict) else {}
    provider_name = str(memory_block.get("provider") or "").strip() or None

    ready = False
    readiness = getattr(request.app.state, "readiness", None)
    if readiness is not None and provider_name:
        row = readiness.readiness.get(f"memory:{provider_name}")
        if row is not None:
            ready = bool(row.ready)
    return {"provider": provider_name, "ready": ready}


def _pilot_compose_versions(request: Request) -> dict[str, Any]:
    """Versions of the three components shipped in this container.

    - ``station``: hermes-station's own package version.
    - ``webui``: the hermes-webui tag baked in at image build time, surfaced via
      the ``HERMES_WEBUI_VERSION`` env var the Dockerfile sets on the final stage.
    - ``hermes``: hermes-agent's installed package version.

    Any field that can't be resolved degrades to ``None`` rather than failing
    the whole compose.
    """
    station = getattr(hermes_station, "__version__", None) or None
    if station == "unknown":
        station = None

    webui = os.environ.get("HERMES_WEBUI_VERSION") or None

    try:
        hermes = _pkg_version("hermes-agent")
    except PackageNotFoundError:
        hermes = None

    return {"station": station, "webui": webui, "hermes": hermes}


async def api_pilot_status(request: Request) -> Response:
    """Read-only status snapshot for the pilot admin extension.

    Registered unconditionally; returns 404 when the pilot flag is off so the
    route table stays consistent across deploys.

    Auth: accepts either a webui ``hermes_session`` (via the bridge) or the
    legacy ``hermes_station_admin`` cookie. Mirrors ``api_ping``'s dual-cookie
    pattern.

    Concurrency: each top-level field is composed in an independent try/except.
    A mid-write ``gateway_state.json`` or transient read error degrades that
    one field to null/empty and logs at INFO; the overall response still 200s.
    """
    if not pilot_admin_extension_enabled():
        return JSONResponse({"error": "not_found"}, status_code=404)

    if not await verify_webui_session(request):
        if not is_authenticated(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    payload: dict[str, Any] = {"ok": True}

    try:
        payload["gateway"] = _pilot_compose_gateway(request)
    except Exception as exc:  # noqa: BLE001
        logger.info("pilot status: gateway compose failed: %s", exc)
        payload["gateway"] = None

    try:
        payload["webui"] = _pilot_compose_webui(request)
    except Exception as exc:  # noqa: BLE001
        logger.info("pilot status: webui compose failed: %s", exc)
        payload["webui"] = None

    try:
        payload["provider"] = _pilot_compose_provider(request)
    except Exception as exc:  # noqa: BLE001
        logger.info("pilot status: provider compose failed: %s", exc)
        payload["provider"] = None

    try:
        payload["channels"] = _pilot_compose_channels(request)
    except Exception as exc:  # noqa: BLE001
        logger.info("pilot status: channels compose failed: %s", exc)
        payload["channels"] = None

    try:
        payload["memory"] = _pilot_compose_memory(request)
    except Exception as exc:  # noqa: BLE001
        logger.info("pilot status: memory compose failed: %s", exc)
        payload["memory"] = None

    try:
        payload["versions"] = _pilot_compose_versions(request)
    except Exception as exc:  # noqa: BLE001
        logger.info("pilot status: versions compose failed: %s", exc)
        payload["versions"] = None

    return JSONResponse(payload)


def _same_origin_or_missing(request: Request) -> bool:
    """CSRF defense for state-changing pilot POSTs.

    Returns True iff the request's ``Origin`` header is absent (non-browser
    callers like curl/tests) OR matches the request's own host. Browsers always
    send ``Origin`` on POST, so a cross-site forged POST will carry the
    attacker's origin and fail this check. Same-origin form POSTs and our own
    extension fetch (``credentials: include``) pass.

    ``Referer`` is also consulted as a fallback when ``Origin`` is missing but
    ``Referer`` is present, which covers older browsers and some privacy modes.
    """
    request_host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    if origin:
        try:
            origin_host = urlsplit(origin).netloc
        except ValueError:
            return False
        return bool(origin_host) and origin_host == request_host
    referer = request.headers.get("referer", "")
    if referer:
        try:
            referer_host = urlsplit(referer).netloc
        except ValueError:
            return False
        return bool(referer_host) and referer_host == request_host
    # No Origin and no Referer — accept (non-browser caller such as curl, tests,
    # or the ASGITransport unit-test client).
    return True


async def api_pilot_gateway_restart(request: Request) -> Response:
    """Restart the gateway subprocess (first write-action through the bridge).

    Auth: accepts either a webui ``hermes_session`` (via the bridge) or the
    legacy ``hermes_station_admin`` cookie. Same dual-cookie posture as
    ``api_pilot_status``.

    CSRF posture: this is the first state-changing pilot POST. No project-wide
    CSRF token infrastructure exists yet (the legacy ``/admin/api/gateway/{action}``
    POSTs ship without one). As defense in depth we require POST + a matching
    ``Origin`` (or ``Referer``) header when one is present. Both webui's session
    cookie and the legacy admin cookie are set with ``SameSite=Lax`` so a true
    cross-site POST also can't carry credentials in modern browsers. A proper
    CSRF token scheme is deferred — tracked under issue #74's workshop.

    Returns 200 ``{"ok": true, "restarted_at": "<iso8601>"}`` on success,
    503 when the gateway supervisor is not initialized, or 500 with a generic
    error message (details in logs) on restart failure.
    """
    if not pilot_admin_extension_enabled():
        return JSONResponse({"error": "not_found"}, status_code=404)

    if not await verify_webui_session(request):
        if not is_authenticated(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not _same_origin_or_missing(request):
        logger.warning(
            "pilot gateway restart: cross-origin POST rejected (origin=%r host=%r)",
            request.headers.get("origin", ""),
            request.headers.get("host", ""),
        )
        return JSONResponse({"ok": False, "error": "cross-origin request rejected"}, status_code=403)

    gateway: Gateway | None = getattr(request.app.state, "gateway", None)
    if gateway is None:
        return JSONResponse(
            {"ok": False, "error": "gateway supervisor not initialized"},
            status_code=503,
        )

    # Operator identity for the audit log: webui sessions are opaque on our
    # side, so we surface which cookie path authorized the call rather than a
    # username. Mirrors api_ping's diagnostic vocabulary.
    via = "webui_session" if await verify_webui_session(request) else "station_admin"
    logger.info("pilot gateway restart requested (via=%s)", via)

    try:
        await gateway.restart()
    except Exception as exc:  # noqa: BLE001
        logger.exception("pilot gateway restart failed: %s", exc)
        return JSONResponse(
            {"ok": False, "error": "restart failed — check logs for details."},
            status_code=500,
        )

    restarted_at = datetime.now(timezone.utc).isoformat()
    logger.info("pilot gateway restart completed at %s (via=%s)", restarted_at, via)
    return JSONResponse({"ok": True, "restarted_at": restarted_at})


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
        return JSONResponse(
            {"ok": False, "error": "Invalid configuration — check logs for details."}, status_code=400
        )
    seed_env_file_to_os(paths.env_path, paths.config_path)
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
        return JSONResponse(
            {"ok": False, "error": "Invalid configuration — check logs for details."}, status_code=400
        )
    seed_env_file_to_os(paths.env_path, paths.config_path)
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
        return JSONResponse(
            {"ok": False, "error": "Action failed — check logs for details."}, status_code=500
        )
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
        Route("/admin/api/ping", api_ping, methods=["GET"]),
        Route("/admin/api/pilot/status", api_pilot_status, methods=["GET"]),
        Route("/admin/api/pilot/gateway/restart", api_pilot_gateway_restart, methods=["POST"]),
        Route("/admin/api/provider/setup", api_provider_setup, methods=["POST"]),
        Route("/admin/api/channels", api_channels_get, methods=["GET"]),
        Route("/admin/api/channels/save", api_channels_save, methods=["POST"]),
        Route("/admin/api/pairing/pending", api_pairing_pending, methods=["GET"]),
        Route("/admin/api/pairing/approved", api_pairing_approved, methods=["GET"]),
        Route("/admin/api/pairing/{action}", api_pairing_action, methods=["GET", "POST"]),
        Route("/admin/api/gateway/{action}", api_gateway_action, methods=["POST"]),
        Route("/admin/api/webui/{action}", api_webui_action, methods=["POST"]),
    ]
