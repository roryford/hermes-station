"""Tests for GET /admin/api/pilot/upgrade — read-only upgrade visibility card.

Covers:
- flag-off → 404
- unauthenticated → 401
- JSON shape with mocked GitHub fetch (no real network calls)
- "current" / "behind" / "unknown" status computation
- 30-minute cache behaviour
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from hermes_station.admin.routes import admin_routes


# ─────────────────────────────────────────────────────────── helpers


async def _login(client: httpx.AsyncClient, password: str) -> None:
    resp = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert resp.status_code == 302, resp.text


def _make_app(monkeypatch, *, flag_on: bool = True):
    if flag_on:
        monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    else:
        monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)

    from hermes_station.app import create_app

    return create_app()


# ─────────────────────────────────────────────────────────── flag-off


async def test_upgrade_flag_off_returns_404(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    app = _make_app(monkeypatch, flag_on=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/api/pilot/upgrade")

    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found"}


# ─────────────────────────────────────────────────────────── auth


async def test_upgrade_unauthenticated_returns_401(
    fake_data_dir: Path, monkeypatch
) -> None:
    app = _make_app(monkeypatch, flag_on=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/pilot/upgrade")

    assert resp.status_code == 401


# ─────────────────────────────────────────────────────────── JSON shape


async def test_upgrade_returns_required_keys(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """Response must include running_version, latest_version, and status."""
    app = _make_app(monkeypatch, flag_on=True)
    transport = httpx.ASGITransport(app=app)
    with patch(
        "hermes_station.admin.routes._fetch_station_latest",
        new=AsyncMock(return_value="v1.0.0"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.get("/admin/api/pilot/upgrade")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "running_version" in data
    assert "latest_version" in data
    assert "status" in data
    assert data["status"] in ("current", "behind", "ahead", "unknown")


# ─────────────────────────────────────────────────────────── status values


async def test_upgrade_status_current_when_versions_match(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """When running == latest (after v-strip), status is 'current'."""
    from hermes_station.admin import routes as _routes

    app = _make_app(monkeypatch, flag_on=True)
    # Inject the running version directly so the test is hermetic.
    monkeypatch.setattr(_routes, "_pkg_version", lambda _name: "1.2.3")

    transport = httpx.ASGITransport(app=app)
    with patch(
        "hermes_station.admin.routes._fetch_station_latest",
        new=AsyncMock(return_value="v1.2.3"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.get("/admin/api/pilot/upgrade")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "current"


async def test_upgrade_status_behind_when_versions_differ(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """When running differs from latest, status is 'behind'."""
    from hermes_station.admin import routes as _routes

    app = _make_app(monkeypatch, flag_on=True)
    monkeypatch.setattr(_routes, "_pkg_version", lambda _name: "1.0.0")

    transport = httpx.ASGITransport(app=app)
    with patch(
        "hermes_station.admin.routes._fetch_station_latest",
        new=AsyncMock(return_value="v2.0.0"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.get("/admin/api/pilot/upgrade")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "behind"


async def test_upgrade_status_unknown_when_latest_is_none(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """When the GitHub fetch fails (returns None), status is 'unknown'."""
    from hermes_station.admin import routes as _routes

    app = _make_app(monkeypatch, flag_on=True)
    monkeypatch.setattr(_routes, "_pkg_version", lambda _name: "1.0.0")

    transport = httpx.ASGITransport(app=app)
    with patch(
        "hermes_station.admin.routes._fetch_station_latest",
        new=AsyncMock(return_value=None),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.get("/admin/api/pilot/upgrade")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "unknown"
    assert data["latest_version"] is None


async def test_upgrade_status_unknown_when_running_is_none(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """When the running version can't be determined, status is 'unknown'."""
    from importlib.metadata import PackageNotFoundError as _PNFError

    from hermes_station.admin import routes as _routes

    app = _make_app(monkeypatch, flag_on=True)
    monkeypatch.setattr(_routes, "_pkg_version", lambda _name: (_ for _ in ()).throw(_PNFError("hermes-station")))

    transport = httpx.ASGITransport(app=app)
    with patch(
        "hermes_station.admin.routes._fetch_station_latest",
        new=AsyncMock(return_value="v2.0.0"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.get("/admin/api/pilot/upgrade")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "unknown"
    assert data["running_version"] is None


# ─────────────────────────────────────────────────────────── cache


async def test_upgrade_cache_prevents_second_fetch(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """A second request within the TTL must not invoke _fetch_station_latest again."""
    app = _make_app(monkeypatch, flag_on=True)
    transport = httpx.ASGITransport(app=app)
    mock_fetch = AsyncMock(return_value="v1.0.0")
    with patch("hermes_station.admin.routes._fetch_station_latest", new=mock_fetch):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            await client.get("/admin/api/pilot/upgrade")
            count_after_first = mock_fetch.call_count
            await client.get("/admin/api/pilot/upgrade")
            count_after_second = mock_fetch.call_count

    assert count_after_second == count_after_first, (
        "Second request within TTL must hit the cache, not re-fetch"
    )


async def test_upgrade_cache_refreshes_after_ttl(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """After TTL expiry the next request should re-fetch."""
    app = _make_app(monkeypatch, flag_on=True)
    transport = httpx.ASGITransport(app=app)
    mock_fetch = AsyncMock(return_value="v1.0.0")
    with patch("hermes_station.admin.routes._fetch_station_latest", new=mock_fetch):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            await client.get("/admin/api/pilot/upgrade")
            count_after_first = mock_fetch.call_count

            # Back-date the cache timestamp past the TTL.
            from hermes_station.admin import routes as _routes

            app.state._pilot_upgrade_cache = {
                "latest": "v1.0.0",
                "ts": time.monotonic() - _routes._PILOT_UPGRADE_CACHE_TTL - 1,
            }

            await client.get("/admin/api/pilot/upgrade")
            count_after_second = mock_fetch.call_count

    assert count_after_second > count_after_first, (
        "Request after TTL expiry must re-fetch from GitHub"
    )


# ─────────────────────────────────────────────────────────── route registration


def test_upgrade_route_is_get_only() -> None:
    """Only GET (and implicit HEAD) is registered for the upgrade endpoint — no POST."""
    for route in admin_routes():
        if route.path == "/admin/api/pilot/upgrade":
            methods = set(route.methods or set())
            # Starlette automatically includes HEAD alongside GET; POST must not be present.
            assert "POST" not in methods, (
                f"upgrade route must not accept POST, got {route.methods}"
            )
            assert "GET" in methods, (
                f"upgrade route must accept GET, got {route.methods}"
            )
            return
    raise AssertionError("upgrade route not registered in admin_routes()")
