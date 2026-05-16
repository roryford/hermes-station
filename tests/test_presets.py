"""Tests for hermes_station.admin.presets — apply_preset logic and HTTP endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.admin.presets import PRESET_CATALOG, apply_preset, routes as presets_routes
from hermes_station.admin.routes import admin_routes
from hermes_station.config import Paths, load_yaml_config, write_yaml_config


# ---------------------------------------------------------------------------
# Catalog sanity
# ---------------------------------------------------------------------------


def test_catalog_has_four_presets() -> None:
    assert len(PRESET_CATALOG) == 4


def test_catalog_ids_are_unique() -> None:
    ids = [p["id"] for p in PRESET_CATALOG]
    assert len(ids) == len(set(ids))


def test_catalog_entries_have_required_keys() -> None:
    required = {"id", "label", "description", "tags", "mcp_enable", "todo"}
    for preset in PRESET_CATALOG:
        assert required <= set(preset.keys()), f"{preset['id']} missing keys"


def test_preset_ids_are_known() -> None:
    known = {"chat_only", "telegram_starter", "research_assistant", "github_helper"}
    assert {p["id"] for p in PRESET_CATALOG} == known


# ---------------------------------------------------------------------------
# Unit: apply_preset
# ---------------------------------------------------------------------------


def test_apply_preset_unknown_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    with pytest.raises(ValueError, match="unknown preset"):
        apply_preset(config_path, "no_such_preset")


def test_apply_preset_no_mcp_enable_returns_preset_without_writing(tmp_path: Path) -> None:
    """chat_only and telegram_starter have empty mcp_enable — no file I/O."""
    config_path = tmp_path / "config.yaml"
    # File does not exist at all; apply should succeed without touching disk.
    preset = apply_preset(config_path, "chat_only")
    assert preset["id"] == "chat_only"
    assert not config_path.exists()


def test_apply_preset_enables_missing_servers(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    preset = apply_preset(config_path, "research_assistant")
    assert preset["id"] == "research_assistant"
    config = load_yaml_config(config_path)
    servers = config["mcp_servers"]
    assert servers["filesystem"]["enabled"] is True
    assert servers["fetch"]["enabled"] is True


def test_apply_preset_enables_server_from_disabled(tmp_path: Path) -> None:
    """A server that exists but is disabled should be flipped to enabled."""
    config_path = tmp_path / "config.yaml"
    write_yaml_config(
        config_path,
        {"mcp_servers": {"filesystem": {"enabled": False, "command": "npx"}}},
    )
    apply_preset(config_path, "research_assistant")
    config = load_yaml_config(config_path)
    assert config["mcp_servers"]["filesystem"]["enabled"] is True


def test_apply_preset_no_clobber_already_enabled(tmp_path: Path) -> None:
    """A server already enabled must not be touched (preserves extra keys)."""
    config_path = tmp_path / "config.yaml"
    write_yaml_config(
        config_path,
        {
            "mcp_servers": {
                "filesystem": {
                    "enabled": True,
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/custom"],
                }
            }
        },
    )
    apply_preset(config_path, "research_assistant")
    config = load_yaml_config(config_path)
    fs = config["mcp_servers"]["filesystem"]
    assert fs["enabled"] is True
    assert fs["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/custom"]


def test_apply_preset_github_helper_enables_both_servers(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    apply_preset(config_path, "github_helper")
    config = load_yaml_config(config_path)
    servers = config["mcp_servers"]
    assert servers["github"]["enabled"] is True
    assert servers["filesystem"]["enabled"] is True


def test_apply_preset_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    apply_preset(config_path, "github_helper")
    mtime1 = config_path.stat().st_mtime
    # Second apply: already enabled, should not rewrite.
    apply_preset(config_path, "github_helper")
    mtime2 = config_path.stat().st_mtime
    assert mtime1 == mtime2


def test_apply_preset_non_dict_mcp_servers_replaced(tmp_path: Path) -> None:
    """If mcp_servers is not a dict (corrupted config), apply should still work."""
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"mcp_servers": None})
    apply_preset(config_path, "research_assistant")
    config = load_yaml_config(config_path)
    assert config["mcp_servers"]["filesystem"]["enabled"] is True


# ---------------------------------------------------------------------------
# Integration: HTTP endpoints
# ---------------------------------------------------------------------------


class _FakeGateway:
    def __init__(self) -> None:
        self.restart_count = 0

    async def restart(self) -> None:
        self.restart_count += 1


def _build_app(gateway: Any | None = None) -> Starlette:
    app_routes: list[Route] = list(presets_routes())
    app_routes.extend(
        route for route in admin_routes() if not (isinstance(route, Route) and route.path == "/admin")
    )
    app = Starlette(routes=app_routes)
    app.state.paths = Paths()
    app.state.webui = None
    app.state.gateway = gateway
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


async def test_presets_page_renders(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/presets")
        assert response.status_code == 200
        body = response.text
        assert "Presets" in body
        assert "Chat-only WebUI" in body
        assert "Telegram bot" in body
        assert "Research assistant" in body
        assert "GitHub helper" in body


async def test_presets_page_redirects_unauthenticated(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/presets", follow_redirects=False)
        assert response.status_code in (302, 303)


async def test_apply_preset_chat_only_returns_applied_card(fake_data_dir: Path, admin_password: str) -> None:
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post("/admin/_partial/presets/chat_only/apply")
        assert response.status_code == 200
        body = response.text
        assert "Applied" in body
        assert "What" in body  # "What's next"


async def test_apply_preset_research_assistant_enables_mcp_and_restarts(
    fake_data_dir: Path, admin_password: str
) -> None:
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)
    transport = httpx.ASGITransport(app=app)
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post("/admin/_partial/presets/research_assistant/apply")
        assert response.status_code == 200
        assert "Applied" in response.text
        config = load_yaml_config(config_path)
        assert config["mcp_servers"]["filesystem"]["enabled"] is True
        assert config["mcp_servers"]["fetch"]["enabled"] is True
        assert gateway.restart_count == 1


async def test_apply_preset_unknown_returns_error_card(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app(gateway=_FakeGateway())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post("/admin/_partial/presets/no_such_preset/apply")
        assert response.status_code == 200
        body = response.text
        assert "Failed" in body or "error" in body.lower()


async def test_apply_preset_unauthenticated_redirects(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/admin/_partial/presets/chat_only/apply", follow_redirects=False)
        assert response.status_code in (302, 303)
