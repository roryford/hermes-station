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
from hermes_station.admin.provider import (
    PROVIDER_CATALOG,
    apply_provider_setup,
    provider_status,
)
from hermes_station.config import Paths, load_env_file, load_yaml_config


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
            "requires_base_url": meta.get("requires_base_url", False),
            "credential_label": meta.get("credential_label", "API key"),
            "credential_placeholder": meta.get("credential_placeholder", "Paste a fresh key — current value is masked"),
            "credential_hint": meta.get(
                "credential_hint",
                "Existing key is preserved unless you enter a new one. Saving with an empty key returns an error.",
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
                "credential_placeholder", "Paste a fresh key — current value is masked"
            ),
            "credential_hint": selected_meta.get(
                "credential_hint",
                "Existing key is preserved unless you enter a new one. Saving with an empty key returns an error.",
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
        # An unchecked/empty field is preserved as-is on the server (do not delete on
        # blank submission, since the form values arrive masked). Only treat an
        # explicit "" with the `__clear__` sentinel as a delete — not used yet.
        if key in form:
            raw = str(form.get(key) or "").strip()
            updates[key] = raw or None
    alert: dict[str, str]
    try:
        save_channel_values(paths.env_path, updates)
        alert = {"kind": "success", "message": "Channels saved."}
    except ValueError as exc:
        alert = {"kind": "error", "message": str(exc)}
    context: dict[str, Any] = {"alert": alert}
    context.update(_channels_context(paths))
    return _templates.TemplateResponse(request, "admin/_channels_card.html", context)


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
        Route(
            "/admin/_partial/pairing/{action}",
            pairings_fragment_action,
            methods=["POST"],
        ),
    ]
