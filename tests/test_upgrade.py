"""Tests for the /admin/upgrade page and helpers."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.applications import Starlette

from hermes_station.admin.upgrade import (
    _normalise,
    fetch_upgrade_info,
    routes as upgrade_routes,
)
from hermes_station.admin.routes import admin_routes


# ──────────────────────────────────────────────────── helpers


def _build_app() -> Starlette:
    """Minimal test app: login + upgrade routes, no proxy/supervisors."""
    app = Starlette(routes=[*admin_routes(), *upgrade_routes()])
    app.state.readiness = None
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


# ──────────────────────────────────────────────── _normalise


def test_normalise_strips_leading_v() -> None:
    assert _normalise("v1.2.3") == "1.2.3"


def test_normalise_no_v_unchanged() -> None:
    assert _normalise("1.2.3") == "1.2.3"


def test_normalise_none_returns_empty() -> None:
    assert _normalise(None) == ""


def test_normalise_empty_returns_empty() -> None:
    assert _normalise("") == ""


def test_normalise_v_only() -> None:
    assert _normalise("v") == ""


# ──────────────────────────────────────────── fetch_upgrade_info


async def test_fetch_upgrade_info_ok_status() -> None:
    """When current matches latest, status should be 'ok'."""
    with patch(
        "hermes_station.admin.upgrade._fetch_latest",
        new=AsyncMock(return_value="v0.2.4"),
    ):
        rows = await fetch_upgrade_info(
            {
                "hermes_station": "0.2.4",
                "hermes_agent": "2026.5.16",
                "hermes_webui": "v0.51.74",
            }
        )

    by_key = {r["key"]: r for r in rows}
    assert by_key["hermes_station"]["status"] == "ok"


async def test_fetch_upgrade_info_update_available() -> None:
    """When current differs from latest, status should be 'update_available'."""
    with patch(
        "hermes_station.admin.upgrade._fetch_latest",
        new=AsyncMock(return_value="v0.3.0"),
    ):
        rows = await fetch_upgrade_info(
            {"hermes_station": "0.2.4", "hermes_agent": None, "hermes_webui": None}
        )

    by_key = {r["key"]: r for r in rows}
    assert by_key["hermes_station"]["status"] == "update_available"


async def test_fetch_upgrade_info_unknown_when_latest_none() -> None:
    """When _fetch_latest returns None, status should be 'unknown'."""
    with patch(
        "hermes_station.admin.upgrade._fetch_latest",
        new=AsyncMock(return_value=None),
    ):
        rows = await fetch_upgrade_info({"hermes_station": "0.2.4", "hermes_agent": "x", "hermes_webui": "y"})

    assert all(r["status"] == "unknown" for r in rows)


async def test_fetch_upgrade_info_unknown_when_current_missing() -> None:
    """When current version is absent from the dict, status should be 'unknown'."""
    with patch(
        "hermes_station.admin.upgrade._fetch_latest",
        new=AsyncMock(return_value="v1.0.0"),
    ):
        rows = await fetch_upgrade_info({})

    assert all(r["status"] == "unknown" for r in rows)


async def test_fetch_upgrade_info_row_structure() -> None:
    """Every row should include the required keys."""
    with patch(
        "hermes_station.admin.upgrade._fetch_latest",
        new=AsyncMock(return_value="v1.0.0"),
    ):
        rows = await fetch_upgrade_info(
            {"hermes_station": "1.0.0", "hermes_agent": "1.0.0", "hermes_webui": "v1.0.0"}
        )

    required_keys = {"key", "label", "current", "latest", "status", "release_url"}
    for row in rows:
        assert required_keys.issubset(row.keys()), f"row missing keys: {row}"


# ──────────────────────────────────────────── upgrade_check endpoint


async def test_upgrade_check_unauthenticated_returns_401(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/_partial/upgrade/check")
    assert resp.status_code == 401


async def test_upgrade_check_authenticated_returns_html(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    with patch(
        "hermes_station.admin.upgrade._fetch_latest",
        new=AsyncMock(return_value="v0.2.4"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post("/admin/_partial/upgrade/check")

    assert resp.status_code == 200
    assert "upgrade-table" in resp.text
    assert "hermes-station" in resp.text


async def test_upgrade_check_contains_status_badge(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    # Inject a fake readiness object so current versions are non-"unknown".
    from types import SimpleNamespace

    app.state.readiness = SimpleNamespace(
        versions={
            "hermes_station": "0.2.4",
            "hermes_agent": "2026.5.16",
            "hermes_webui": "v0.51.74",
        }
    )
    transport = httpx.ASGITransport(app=app)
    with patch(
        "hermes_station.admin.upgrade._fetch_latest",
        new=AsyncMock(return_value="v99.0.0"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post("/admin/_partial/upgrade/check")

    assert resp.status_code == 200
    assert "update available" in resp.text


# ──────────────────────────────────────────── cache behaviour


async def test_upgrade_check_cache_prevents_second_fetch(fake_data_dir: Path, admin_password: str) -> None:
    """A second call within the TTL should not invoke _fetch_latest again."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    mock_fetch = AsyncMock(return_value="v0.2.4")
    with patch("hermes_station.admin.upgrade._fetch_latest", new=mock_fetch):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            await client.post("/admin/_partial/upgrade/check")
            call_count_after_first = mock_fetch.call_count
            await client.post("/admin/_partial/upgrade/check")
            call_count_after_second = mock_fetch.call_count

    # The cache should have been hit on the second call — no additional fetches.
    assert call_count_after_second == call_count_after_first


async def test_upgrade_check_cache_refreshes_after_ttl(
    fake_data_dir: Path, admin_password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After TTL expires, the next call should re-fetch."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    mock_fetch = AsyncMock(return_value="v0.2.4")

    with patch("hermes_station.admin.upgrade._fetch_latest", new=mock_fetch):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            await client.post("/admin/_partial/upgrade/check")
            call_count_after_first = mock_fetch.call_count

            # Simulate cache expiry by back-dating the stored timestamp.

            old_cache = app.state._upgrade_cache
            app.state._upgrade_cache = {"rows": old_cache["rows"], "ts": time.monotonic() - 1900}

            await client.post("/admin/_partial/upgrade/check")
            call_count_after_second = mock_fetch.call_count

    assert call_count_after_second > call_count_after_first


# ──────────────────────────────────────────── upgrade page GET


async def test_upgrade_page_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/upgrade", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"


async def test_upgrade_page_renders_after_login(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/upgrade")
    assert resp.status_code == 200
    assert "Upgrade" in resp.text
    assert "Check for updates" in resp.text
