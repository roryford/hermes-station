"""HTMX page + fragment handlers for the admin Settings and Pairings pages.

This module renders Jinja2 templates against the same underlying domain helpers
(`provider.apply_provider_setup`, `channels.save_channel_values`, the pairing
approve/deny/revoke functions) used by the JSON API in `admin/routes.py`.

Design choice — fragment endpoints over `hx-ext='json-enc'`:
The existing JSON API endpoints return JSON, which is awkward to swap into
the DOM. Instead we expose form-encoded fragment endpoints under
`/admin/_partial/*` that call the same domain helpers and return HTML
snippets ready for `hx-swap`. This is option (a) in the task brief — slightly
more code than the json-enc alternative but more idiomatic HTMX and avoids
loading the `htmx-ext-json-enc` script in the base template.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import require_admin
from hermes_station.admin.channels import (
    CHANNEL_CATALOG,
    CHANNEL_ENV_KEYS,
    channel_status,
    save_channel_values,
)
from hermes_station.admin.pairing import (
    approve,
    deny,
    get_approved,
    get_pending,
    revoke,
)
from hermes_station.admin.copilot_oauth import poll_device_flow, start_device_flow
from hermes_station.admin.provider import (
    PROVIDER_CATALOG,
    apply_provider_setup,
    provider_status,
)
from hermes_station.config import Paths, load_env_file, load_yaml_config, seed_env_file_to_os


def _paths(request: Request) -> Paths:
    return request.app.state.paths


def _provider_context(paths: Paths) -> dict[str, Any]:
    config = load_yaml_config(paths.config_path)
    env_values = load_env_file(paths.env_path)
    status = provider_status(config, env_values)
    selected_provider = status["provider"].lower()
    if selected_provider not in PROVIDER_CATALOG:
        selected_provider = next(iter(PROVIDER_CATALOG))
    selected_meta = PROVIDER_CATALOG[selected_provider]
    catalog = [
        {
            "id": pid,
            "label": meta["label"],
            "default_model": meta.get("default_model", ""),
            "requires_base_url": meta.get("requires_base_url", False),
            "credential_label": meta.get("credential_label", "API key"),
            "credential_placeholder": meta.get("credential_placeholder", "Leave blank to keep existing key"),
            "credential_hint": meta.get(
                "credential_hint",
                "Leave blank to keep the stored key. Required only for first setup or key rotation.",
            ),
        }
        for pid, meta in PROVIDER_CATALOG.items()
    ]
    label = PROVIDER_CATALOG.get(status["provider"].lower(), {}).get("label", "")
    return {
        "provider_catalog": catalog,
        "provider_status": status,
        "provider_label": label,
        "provider_form_meta": {
            "credential_label": selected_meta.get("credential_label", "API key"),
            "credential_placeholder": selected_meta.get(
                "credential_placeholder", "Leave blank to keep existing key"
            ),
            "credential_hint": selected_meta.get(
                "credential_hint",
                "Leave blank to keep the stored key. Required only for first setup or key rotation.",
            ),
        },
    }


def _channels_context(paths: Paths) -> dict[str, Any]:
    env_values = load_env_file(paths.env_path)
    return {"channels": channel_status(env_values)}


def _pairings_context(paths: Paths) -> dict[str, Any]:
    return {
        "pending": get_pending(paths.pairing_dir),
        "approved": get_approved(paths.pairing_dir),
    }


# ───────────────────────────────────────────────────────────────── pages


async def settings_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    context: dict[str, Any] = {"active": "settings"}
    context.update(_provider_context(paths))
    context.update(_channels_context(paths))
    return _templates.TemplateResponse(request, "admin/settings.html", context)


async def pairings_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    return _templates.TemplateResponse(
        request,
        "admin/pairings.html",
        {"active": "pairings"},
    )


async def pairings_fragment(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    return _templates.TemplateResponse(
        request,
        "admin/_pairings_panel.html",
        _pairings_context(paths),
    )


# ───────────────────────────────────────────────────────────── partial POSTs


async def provider_fragment_save(request: Request) -> Response:
    """Form-encoded provider save. Returns the refreshed provider card."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    form = await request.form()
    alert: dict[str, str] | None = None
    try:
        apply_provider_setup(
            config_path=paths.config_path,
            env_path=paths.env_path,
            provider=str(form.get("provider") or ""),
            model=str(form.get("model") or ""),
            api_key=str(form.get("api_key") or ""),
            base_url=str(form.get("base_url") or ""),
        )
        seed_env_file_to_os(paths.env_path)
        gateway = getattr(request.app.state, "gateway", None)
        if gateway is not None:
            await gateway.restart()
        alert = {"kind": "success", "message": "Provider saved."}
    except ValueError as exc:
        alert = {"kind": "error", "message": str(exc)}
    context: dict[str, Any] = {"alert": alert}
    context.update(_provider_context(paths))
    return _templates.TemplateResponse(request, "admin/_provider_card.html", context)


async def channels_fragment_save(request: Request) -> Response:
    """Form-encoded channels save. Returns the refreshed channels card."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    form = await request.form()
    updates: dict[str, str | None] = {}
    for key in CHANNEL_ENV_KEYS:
        # Blank field = keep existing value. Only update if the user typed something.
        if key in form:
            raw = str(form.get(key) or "").strip()
            if raw:
                updates[key] = raw
    alert: dict[str, str]
    try:
        save_channel_values(paths.env_path, updates)
        seed_env_file_to_os(paths.env_path)
        gateway = getattr(request.app.state, "gateway", None)
        if gateway is not None:
            await gateway.restart()
        alert = {"kind": "success", "message": "Channels saved."}
    except ValueError as exc:
        alert = {"kind": "error", "message": str(exc)}
    context: dict[str, Any] = {"alert": alert}
    context.update(_channels_context(paths))
    return _templates.TemplateResponse(request, "admin/_channels_card.html", context)


async def channels_fragment_clear(request: Request) -> Response:
    """Clear all env keys for a single channel by slug."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    form = await request.form()
    slug = str(form.get("slug") or "").strip()
    entry = next((c for c in CHANNEL_CATALOG if c["slug"] == slug), None)
    alert: dict[str, str]
    if entry:
        updates: dict[str, str | None] = {entry["primary_key"]: None}
        if entry["secondary_key"]:
            updates[entry["secondary_key"]] = None
        try:
            save_channel_values(paths.env_path, updates)
            seed_env_file_to_os(paths.env_path)
            gateway = getattr(request.app.state, "gateway", None)
            if gateway is not None:
                await gateway.restart()
            alert = {"kind": "success", "message": f"{entry['label']} cleared."}
        except ValueError as exc:
            alert = {"kind": "error", "message": str(exc)}
    else:
        alert = {"kind": "error", "message": "Unknown channel slug."}
    context: dict[str, Any] = {"alert": alert}
    context.update(_channels_context(paths))
    return _templates.TemplateResponse(request, "admin/_channels_card.html", context)


async def channels_fragment_toggle(request: Request) -> Response:
    """Toggle the disabled flag for a single channel by slug."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    form = await request.form()
    slug = str(form.get("slug") or "").strip()
    entry = next((c for c in CHANNEL_CATALOG if c["slug"] == slug), None)
    alert: dict[str, str]
    if entry and entry.get("disable_key"):
        disable_key = entry["disable_key"]
        env_values = load_env_file(paths.env_path)
        currently_disabled = env_values.get(disable_key, "").strip().lower() in {"1", "true", "yes", "on"}
        updates: dict[str, str | None] = {disable_key: None if currently_disabled else "1"}
        try:
            save_channel_values(paths.env_path, updates)
            seed_env_file_to_os(paths.env_path)
            gateway = getattr(request.app.state, "gateway", None)
            if gateway is not None:
                await gateway.restart()
            label = entry["label"]
            verb = "enabled" if currently_disabled else "disabled"
            alert = {"kind": "success", "message": f"{label} {verb}."}
        except ValueError as exc:
            alert = {"kind": "error", "message": str(exc)}
    else:
        alert = {"kind": "error", "message": "Unknown channel or toggle not supported."}
    context: dict[str, Any] = {"alert": alert}
    context.update(_channels_context(paths))
    return _templates.TemplateResponse(request, "admin/_channels_card.html", context)


async def copilot_oauth_start(request: Request) -> Response:
    """Initiate the GitHub Copilot device code flow. Returns the device flow card."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    try:
        flow = await start_device_flow()
    except Exception as exc:
        context: dict[str, Any] = {
            "alert": {"kind": "error", "message": f"Could not start GitHub OAuth: {exc}"}
        }
        context.update(_provider_context(paths))
        return _templates.TemplateResponse(request, "admin/_provider_card.html", context)
    return _templates.TemplateResponse(
        request,
        "admin/_copilot_device_flow.html",
        {
            "device_code": flow["device_code"],
            "user_code": flow["user_code"],
            "verification_uri": flow.get("verification_uri", "https://github.com/login/device"),
            "poll_interval": flow["poll_interval"],
        },
    )


async def copilot_oauth_poll(request: Request) -> Response:
    """Poll GitHub for the Copilot OAuth token. Returns device flow card or provider card."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    form = await request.form()
    device_code = str(form.get("device_code") or "").strip()
    try:
        interval = max(5, min(int(str(form.get("interval") or 8)), 60))
    except (ValueError, TypeError):
        interval = 8
    user_code = str(form.get("user_code") or "").strip()
    verification_uri = str(form.get("verification_uri") or "https://github.com/login/device").strip()

    if not device_code:
        context: dict[str, Any] = {"alert": {"kind": "error", "message": "Missing device_code."}}
        context.update(_provider_context(paths))
        return _templates.TemplateResponse(request, "admin/_provider_card.html", context)

    try:
        result = await poll_device_flow(device_code, interval=interval)
    except Exception as exc:
        context = {"alert": {"kind": "error", "message": f"Poll error: {exc}"}}
        context.update(_provider_context(paths))
        return _templates.TemplateResponse(request, "admin/_provider_card.html", context)

    status = result["status"]

    if status in ("pending", "slow_down"):
        return _templates.TemplateResponse(
            request,
            "admin/_copilot_device_flow.html",
            {
                "device_code": device_code,
                "user_code": user_code,
                "verification_uri": verification_uri,
                "poll_interval": result["poll_interval"],
            },
        )

    if status == "success":
        token = result["token"]
        meta = PROVIDER_CATALOG["copilot"]
        try:
            apply_provider_setup(
                config_path=paths.config_path,
                env_path=paths.env_path,
                provider="copilot",
                model=meta["default_model"],
                api_key=token,
            )
            seed_env_file_to_os(paths.env_path)
            from hermes_station.gateway import Gateway as _Gateway

            gateway: _Gateway = request.app.state.gateway
            await gateway.restart()
            alert: dict[str, str] = {"kind": "success", "message": "GitHub Copilot connected."}
        except Exception as exc:
            alert = {"kind": "error", "message": f"Token received but could not save: {exc}"}
        context = {"alert": alert}
        context.update(_provider_context(paths))
        return _templates.TemplateResponse(request, "admin/_provider_card.html", context)

    # expired / denied / error
    context = {"alert": {"kind": "error", "message": result.get("message", "Authorization failed.")}}
    context.update(_provider_context(paths))
    return _templates.TemplateResponse(request, "admin/_provider_card.html", context)


async def provider_cancel(request: Request) -> Response:
    """Cancel an in-progress provider flow. Returns the plain provider card."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    context: dict[str, Any] = {}
    context.update(_provider_context(paths))
    return _templates.TemplateResponse(request, "admin/_provider_card.html", context)


async def pairings_fragment_action(request: Request) -> Response:
    """Form-encoded approve/deny/revoke. Returns the refreshed pairings panel."""
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    action = request.path_params["action"]
    form = await request.form()
    user_id = str(form.get("user_id") or "").strip()
    if user_id and action in {"approve", "deny", "revoke"}:
        try:
            if action == "approve":
                approve(paths.pairing_dir, user_id)
            elif action == "deny":
                deny(paths.pairing_dir, user_id)
            else:
                revoke(paths.pairing_dir, user_id)
        except (KeyError, ValueError):
            # Swallow — the refreshed panel reflects reality either way.
            pass
    return _templates.TemplateResponse(
        request,
        "admin/_pairings_panel.html",
        _pairings_context(paths),
    )


def routes() -> list[Route]:
    return [
        Route("/admin/settings", settings_page, methods=["GET"]),
        Route("/admin/pairings", pairings_page, methods=["GET"]),
        Route("/admin/_partial/pairings", pairings_fragment, methods=["GET"]),
        Route("/admin/_partial/provider/setup", provider_fragment_save, methods=["POST"]),
        Route("/admin/_partial/channels/save", channels_fragment_save, methods=["POST"]),
        Route("/admin/_partial/channels/clear", channels_fragment_clear, methods=["POST"]),
        Route("/admin/_partial/channels/toggle", channels_fragment_toggle, methods=["POST"]),
        Route("/admin/_partial/provider/copilot/start", copilot_oauth_start, methods=["POST"]),
        Route("/admin/_partial/provider/copilot/poll", copilot_oauth_poll, methods=["POST"]),
        Route("/admin/_partial/provider/cancel", provider_cancel, methods=["POST"]),
        Route(
            "/admin/_partial/pairing/{action}",
            pairings_fragment_action,
            methods=["POST"],
        ),
    ]
