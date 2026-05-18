"""Tests for the Layer-B pilot admin extension feature.

Covers:
- ``GET /admin/api/pilot/status`` behavior (flag-off 404, flag-on schema,
  unauthenticated 401, concurrency-safe field composition).
- Auto-seeding of ``HERMES_WEBUI_EXTENSION_*`` env vars when the pilot flag is
  on, with operator-set values taking precedence.

The pilot status endpoint is registered at ``/admin/api/pilot/status``
(not ``/admin/api/status``) to avoid clobbering the legacy contract endpoint
documented in ``docs/CONTRACT.md`` §5.1. See Worker 2's deviation note.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from hermes_station.webui import WebUIProcess


# ─────────────────────────────────────────────────────────── helpers


def _make_webui(fake_data_dir: Path) -> WebUIProcess:
    return WebUIProcess(
        webui_src=fake_data_dir / "no-webui",
        hermes_home=fake_data_dir / ".hermes",
        webui_state_dir=fake_data_dir / "webui",
        workspace_dir=fake_data_dir / "workspace",
        config_path=fake_data_dir / ".hermes" / "config.yaml",
    )


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post(
        "/admin/login",
        data={"password": password},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text


# ─────────────────────────────────────────────────────────── auto-seeding


def test_autoseed_flag_off_no_env_vars(fake_data_dir: Path, monkeypatch) -> None:
    monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_DIR", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_SCRIPT_URLS", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_STYLESHEET_URLS", raising=False)

    env = _make_webui(fake_data_dir)._build_env()

    assert "HERMES_WEBUI_EXTENSION_DIR" not in env
    assert "HERMES_WEBUI_EXTENSION_SCRIPT_URLS" not in env
    assert "HERMES_WEBUI_EXTENSION_STYLESHEET_URLS" not in env


def test_autoseed_flag_on_defaults_seeded(
    fake_data_dir: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_DIR", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_SCRIPT_URLS", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_STYLESHEET_URLS", raising=False)

    caplog.set_level(logging.WARNING, logger="hermes_station.webui")
    env = _make_webui(fake_data_dir)._build_env()

    assert env["HERMES_WEBUI_EXTENSION_DIR"] == "/opt/hermes-station/extension"
    assert env["HERMES_WEBUI_EXTENSION_SCRIPT_URLS"] == "/extensions/admin.js"
    assert env["HERMES_WEBUI_EXTENSION_STYLESHEET_URLS"] == "/extensions/admin.css"
    # Happy path: no WARNING about operator overrides.
    assert not any("already set" in rec.message for rec in caplog.records)


def test_autoseed_operator_value_wins(
    fake_data_dir: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    monkeypatch.setenv("HERMES_WEBUI_EXTENSION_DIR", "/my/bundle")
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_SCRIPT_URLS", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_EXTENSION_STYLESHEET_URLS", raising=False)

    caplog.set_level(logging.WARNING, logger="hermes_station.webui")
    env = _make_webui(fake_data_dir)._build_env()

    # Operator value preserved.
    assert env["HERMES_WEBUI_EXTENSION_DIR"] == "/my/bundle"
    # Other two keys still get defaults.
    assert env["HERMES_WEBUI_EXTENSION_SCRIPT_URLS"] == "/extensions/admin.js"
    assert env["HERMES_WEBUI_EXTENSION_STYLESHEET_URLS"] == "/extensions/admin.css"

    warning_messages = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("HERMES_WEBUI_EXTENSION_DIR" in m and "already set" in m for m in warning_messages)
    # Only the one pre-set key warns; not the other two.
    assert not any("HERMES_WEBUI_EXTENSION_SCRIPT_URLS" in m and "already set" in m for m in warning_messages)
    assert not any(
        "HERMES_WEBUI_EXTENSION_STYLESHEET_URLS" in m and "already set" in m for m in warning_messages
    )


# ─────────────────────────────────────────────────────────── status endpoint


async def test_status_flag_off_returns_404(fake_data_dir: Path, admin_password: str, monkeypatch) -> None:
    monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/api/pilot/status")

    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found"}


async def test_status_unauthenticated_returns_401(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/pilot/status")

    assert resp.status_code == 401


async def test_status_flag_on_returns_schema(fake_data_dir: Path, admin_password: str, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/api/pilot/status")

    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Top-level shape.
    assert data["ok"] is True
    for key in ("gateway", "webui", "provider", "channels", "memory"):
        assert key in data, f"missing top-level key {key!r}"

    # Per-field types (best-effort — null is permitted on compose failure).
    if data["gateway"] is not None:
        assert isinstance(data["gateway"], dict)
        for k in ("state", "pid", "uptime_s", "platform", "connection"):
            assert k in data["gateway"]
    if data["webui"] is not None:
        assert isinstance(data["webui"], dict)
        assert "state" in data["webui"]
        assert "pid" in data["webui"]
    if data["provider"] is not None:
        assert isinstance(data["provider"], dict)
        assert "name" in data["provider"]
        assert "model" in data["provider"]
    if data["channels"] is not None:
        assert isinstance(data["channels"], list)
        for entry in data["channels"]:
            assert {"name", "intended", "ready", "reason"}.issubset(entry.keys())
            assert isinstance(entry["intended"], bool)
            assert isinstance(entry["ready"], bool)
    if data["memory"] is not None:
        assert isinstance(data["memory"], dict)
        assert "provider" in data["memory"]
        assert "ready" in data["memory"]
        assert isinstance(data["memory"]["ready"], bool)


async def test_status_concurrency_safe_under_mid_write(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """If a composition helper raises (mid-write race), the response still 200s
    with that field set to null."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.admin import routes as _routes

    def _boom(_request):  # type: ignore[no-untyped-def]
        raise OSError("simulated mid-write of gateway_state.json")

    monkeypatch.setattr(_routes, "_pilot_compose_gateway", _boom)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/api/pilot/status")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["gateway"] is None
    # Other fields still composed.
    assert "webui" in data
    assert "provider" in data
    assert "channels" in data
    assert "memory" in data
