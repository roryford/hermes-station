"""Tests for channel-related routes and helpers."""

from __future__ import annotations

from pathlib import Path

import httpx


# ─────────────────────────────────────────────────────────── API channel routes


async def test_api_channels_get_returns_list(fake_data_dir: Path, admin_password: str) -> None:
    """Channels GET returns a list of channels."""
    from hermes_station.app import create_app
    from hermes_station.admin import routes

    routes._login_attempts.clear()

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/channels")
    assert resp.status_code == 200
    assert "channels" in resp.json()
    assert isinstance(resp.json()["channels"], list)


async def test_api_channels_get_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    """api_channels_get returns 401 without auth."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/channels")
    assert resp.status_code == 401


async def test_api_channels_save_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    """api_channels_save returns 401 without auth."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/api/channels/save", json={})
    assert resp.status_code == 401


async def test_channels_save_rejects_invalid_values(fake_data_dir: Path, admin_password: str) -> None:
    """Keys with newlines in values should cause 400."""
    from hermes_station.app import create_app
    from hermes_station.admin import routes

    routes._login_attempts.clear()

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post(
            "/admin/api/channels/save",
            json={"TELEGRAM_BOT_TOKEN": "12345:abc\nevil-inject"},
        )
    assert resp.status_code == 400
