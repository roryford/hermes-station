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
    for key in ("gateway", "webui", "provider", "channels", "memory", "versions"):
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
    if data["versions"] is not None:
        assert isinstance(data["versions"], dict)
        for k in ("station", "webui", "hermes"):
            assert k in data["versions"]


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
    assert "versions" in data


# ─────────────────────────────────────────────────── gateway restart endpoint


class _FakeGateway:
    """Records restart calls. Optionally raises to simulate failure."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls = 0
        self.raises = raises

    async def restart(self) -> None:
        self.calls += 1
        if self.raises is not None:
            raise self.raises


async def test_gateway_restart_flag_off_returns_404(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post("/admin/api/pilot/gateway/restart")

    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found"}


async def test_gateway_restart_unauthenticated_returns_401(fake_data_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/api/pilot/gateway/restart")

    assert resp.status_code == 401


async def test_gateway_restart_supervisor_missing_returns_503(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    # Ensure no gateway is installed on app.state.
    if hasattr(app.state, "gateway"):
        app.state.gateway = None

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post("/admin/api/pilot/gateway/restart")

    assert resp.status_code == 503
    data = resp.json()
    assert data["ok"] is False
    assert "not initialized" in data["error"]


async def test_gateway_restart_success_returns_iso_timestamp(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    fake = _FakeGateway()
    app.state.gateway = fake

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post("/admin/api/pilot/gateway/restart")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert "restarted_at" in data
    # ISO8601 round-trip parses without raising.
    from datetime import datetime

    datetime.fromisoformat(data["restarted_at"])
    assert fake.calls == 1


async def test_gateway_restart_failure_returns_500_generic_error(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    fake = _FakeGateway(raises=RuntimeError("simulated gateway restart failure"))
    app.state.gateway = fake

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post("/admin/api/pilot/gateway/restart")

    assert resp.status_code == 500
    data = resp.json()
    assert data["ok"] is False
    # Error is generic; details only in logs (don't leak internals to the client).
    assert "simulated" not in data["error"]
    assert fake.calls == 1


async def test_gateway_restart_cross_origin_post_rejected(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """Browser POST with a foreign Origin header is rejected as CSRF defense."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    fake = _FakeGateway()
    app.state.gateway = fake

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/pilot/gateway/restart",
            headers={"Origin": "http://evil.example.com"},
        )

    assert resp.status_code == 403
    data = resp.json()
    assert data["ok"] is False
    assert "cross-origin" in data["error"].lower()
    # Gateway must NOT have been touched.
    assert fake.calls == 0


async def test_gateway_restart_same_origin_post_accepted(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """Matching Origin (same as Host) passes the CSRF defense."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    fake = _FakeGateway()
    app.state.gateway = fake

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/pilot/gateway/restart",
            headers={"Origin": "http://test"},
        )

    assert resp.status_code == 200
    assert fake.calls == 1


def test_gateway_restart_route_is_post_only() -> None:
    """Only POST is registered for the restart route — a GET cannot trigger a restart.

    Checked at the route-table level (rather than via an HTTP GET) because the
    app's catch-all proxy route swallows unmatched GETs and forwards them to
    the webui subprocess. The real safety property is: only POST is wired up.
    """
    from hermes_station.admin.routes import admin_routes

    for route in admin_routes():
        if route.path == "/admin/api/pilot/gateway/restart":
            assert set(route.methods or set()) == {"POST"}, (
                f"restart route should be POST-only, got {route.methods}"
            )
            return
    raise AssertionError("restart route not registered in admin_routes()")


# ─────────────────────────────────────────────────── docs-sync regression


_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_readme_documents_pilot_restart_requirement() -> None:
    """README's Pilot features section must state that flag changes require a
    container restart — the webui subprocess captures env at boot, so live
    env changes are not picked up. Pure docs-sync check; not behavior."""
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Pilot features" in readme, "README must document pilot features"
    assert "HERMES_STATION_PILOT_ADMIN_EXTENSION" in readme
    assert "restart" in readme.lower()
    assert "captures" in readme and "boot" in readme, (
        "README must explain that the webui subprocess captures env at boot "
        "so flag changes only take effect after a restart"
    )


def test_contract_documents_pilot_restart_requirement() -> None:
    """docs/CONTRACT.md's Pilot features section must state that flag changes
    require a container restart. Mirror of the README assertion above so both
    docs stay in sync."""
    contract = (_REPO_ROOT / "docs" / "CONTRACT.md").read_text(encoding="utf-8")
    assert "Pilot features" in contract, "CONTRACT.md must document pilot features"
    assert "HERMES_STATION_PILOT_ADMIN_EXTENSION" in contract
    assert "Restart requirement" in contract, (
        "CONTRACT.md must include an explicit 'Restart requirement' note"
    )
    assert "captures its environment at boot" in contract, (
        "CONTRACT.md must explain that the webui subprocess captures env at boot"
    )
