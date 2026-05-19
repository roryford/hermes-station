"""Tests for the /admin/topology page."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from hermes_station.admin.routes import admin_routes
from hermes_station.admin.topology import routes as topology_routes
from hermes_station.config import Paths


def _build_app() -> Starlette:
    """Minimal test app: login + topology routes, no proxy/supervisors."""
    app = Starlette(routes=[*admin_routes(), *topology_routes()])
    app.state.paths = Paths()
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


async def test_topology_page_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/topology", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"


async def test_topology_page_renders_after_login(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/topology")
    assert resp.status_code == 200
    assert "Topology" in resp.text
    assert "hermes-station container" in resp.text
