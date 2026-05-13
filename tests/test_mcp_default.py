"""First-boot default-MCP-servers seeding + admin toggle endpoint.

Mirrors tests/test_memory_default.py. The on-disk schema (top-level
`mcp_servers:` mapping with per-server `command/args/env/enabled`) is owned
by hermes-agent's `tools/mcp_tool.py` — these tests pin the bits we control
(catalog contents, default-off, no-clobber, toggle behavior).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.admin.htmx_dashboard import routes as htmx_routes
from hermes_station.admin.mcp import (
    load_mcp_status,
    mcp_status,
    toggle_mcp_server,
)
from hermes_station.admin.routes import admin_routes
from hermes_station.config import (
    MCP_SERVER_CATALOG,
    Paths,
    load_yaml_config,
    seed_default_mcp_servers,
    write_yaml_config,
)


CATALOG_NAMES = {entry["name"] for entry in MCP_SERVER_CATALOG}
EXPECTED_NAMES = {"filesystem", "fetch", "github"}


# ---------------------------------------------------------------------------
# Catalog sanity
# ---------------------------------------------------------------------------


def test_catalog_contains_the_three_curated_servers() -> None:
    assert CATALOG_NAMES == EXPECTED_NAMES


def test_catalog_entries_are_stdio_only() -> None:
    """Lite-tier policy: stdio only — no `url:` HTTP servers."""
    for entry in MCP_SERVER_CATALOG:
        assert entry["command"], f"{entry['name']}: missing command (stdio required)"
        assert "url" not in entry, f"{entry['name']}: HTTP servers go in full-tier"


# ---------------------------------------------------------------------------
# Unit: seed_default_mcp_servers
# ---------------------------------------------------------------------------


def test_seed_writes_all_three_default_off_when_config_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    assert not config_path.exists()

    added = seed_default_mcp_servers(config_path)

    assert set(added) == EXPECTED_NAMES
    config = load_yaml_config(config_path)
    servers = config["mcp_servers"]
    for name in EXPECTED_NAMES:
        assert servers[name]["enabled"] is False
        assert "command" in servers[name]
        assert "args" in servers[name]


def test_seed_preserves_existing_unrelated_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(
        config_path,
        {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}},
    )

    added = seed_default_mcp_servers(config_path)

    assert set(added) == EXPECTED_NAMES
    config = load_yaml_config(config_path)
    assert config["model"]["provider"] == "anthropic"
    assert set(config["mcp_servers"].keys()) == EXPECTED_NAMES


def test_seed_no_clobber_existing_server_entry(tmp_path: Path) -> None:
    """A pre-existing entry (e.g. user set enabled: true, or custom args)
    must be preserved verbatim — only missing names get added."""
    config_path = tmp_path / "config.yaml"
    write_yaml_config(
        config_path,
        {
            "mcp_servers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/custom/path"],
                    "enabled": True,
                },
            },
        },
    )

    added = seed_default_mcp_servers(config_path)

    # filesystem was already there — only fetch + github were added.
    assert set(added) == {"fetch", "github"}
    config = load_yaml_config(config_path)
    fs = config["mcp_servers"]["filesystem"]
    assert fs["enabled"] is True
    assert fs["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/custom/path"]


def test_seed_no_clobber_user_disabled_custom_server(tmp_path: Path) -> None:
    """A user-added MCP server (not in our catalog) must survive the seed."""
    config_path = tmp_path / "config.yaml"
    write_yaml_config(
        config_path,
        {"mcp_servers": {"my-custom": {"command": "node", "args": ["./srv.js"]}}},
    )

    seed_default_mcp_servers(config_path)

    config = load_yaml_config(config_path)
    assert config["mcp_servers"]["my-custom"]["command"] == "node"
    assert set(config["mcp_servers"].keys()) == EXPECTED_NAMES | {"my-custom"}


def test_seed_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    seed_default_mcp_servers(config_path)
    added_second = seed_default_mcp_servers(config_path)
    assert added_second == []


# ---------------------------------------------------------------------------
# Unit: status + toggle
# ---------------------------------------------------------------------------


def test_status_reflects_disabled_after_seed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    seed_default_mcp_servers(config_path)
    rows = load_mcp_status(config_path, tmp_path / ".env")
    assert {r["name"] for r in rows} == EXPECTED_NAMES
    for r in rows:
        assert r["enabled"] is False
        assert r["configured"] is True


def test_status_marks_github_needs_unsatisfied_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config_path = tmp_path / "config.yaml"
    seed_default_mcp_servers(config_path)
    rows = mcp_status(load_yaml_config(config_path), {})
    github = next(r for r in rows if r["name"] == "github")
    assert github["needs"] == ["GITHUB_TOKEN"]
    assert github["needs_satisfied"] is False


def test_status_marks_github_needs_satisfied_with_env_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config_path = tmp_path / "config.yaml"
    seed_default_mcp_servers(config_path)
    rows = mcp_status(load_yaml_config(config_path), {"GITHUB_TOKEN": "ghp_xxx"})
    github = next(r for r in rows if r["name"] == "github")
    assert github["needs_satisfied"] is True


def test_toggle_flips_enabled_and_persists(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    seed_default_mcp_servers(config_path)

    new_value = toggle_mcp_server(config_path, "filesystem")
    assert new_value is True
    config = load_yaml_config(config_path)
    assert config["mcp_servers"]["filesystem"]["enabled"] is True

    # Toggle back.
    new_value = toggle_mcp_server(config_path, "filesystem")
    assert new_value is False
    config = load_yaml_config(config_path)
    assert config["mcp_servers"]["filesystem"]["enabled"] is False


def test_toggle_unknown_server_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    seed_default_mcp_servers(config_path)
    with pytest.raises(ValueError, match="unknown MCP server"):
        toggle_mcp_server(config_path, "no-such-thing")


def test_toggle_does_not_clobber_other_servers(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(
        config_path,
        {"mcp_servers": {"my-custom": {"command": "node", "args": ["./srv.js"]}}},
    )
    seed_default_mcp_servers(config_path)
    toggle_mcp_server(config_path, "fetch")
    config = load_yaml_config(config_path)
    assert config["mcp_servers"]["my-custom"]["command"] == "node"
    assert config["mcp_servers"]["fetch"]["enabled"] is True


# ---------------------------------------------------------------------------
# Integration: dashboard renders + toggle endpoint
# ---------------------------------------------------------------------------


class _FakeGateway:
    def __init__(self) -> None:
        self.restart_count = 0

    async def restart(self) -> None:
        self.restart_count += 1


def _build_app(gateway: Any | None = None) -> Starlette:
    base_routes: list[Route] = list(htmx_routes())
    base_routes.extend(
        route
        for route in admin_routes()
        if not (isinstance(route, Route) and route.path == "/admin")
    )
    app = Starlette(routes=base_routes)
    app.state.paths = Paths()
    app.state.webui = None
    app.state.gateway = gateway
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post(
        "/admin/login", data={"password": password}, follow_redirects=False
    )
    assert response.status_code == 302, response.text


async def test_dashboard_renders_mcp_card(
    fake_data_dir: Path, admin_password: str
) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    seed_default_mcp_servers(config_path)
    app = _build_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin")
        assert response.status_code == 200
        body = response.text
        assert "MCP servers" in body
        assert "Filesystem" in body
        assert "Web fetch" in body
        assert "GitHub" in body


async def test_toggle_endpoint_flips_and_restarts_gateway(
    fake_data_dir: Path, admin_password: str
) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    seed_default_mcp_servers(config_path)
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/mcp/toggle", data={"name": "filesystem"}
        )
        assert response.status_code == 200
        assert "filesystem" in response.text
        # Card came back with the updated badge.
        config = load_yaml_config(config_path)
        assert config["mcp_servers"]["filesystem"]["enabled"] is True
        assert gateway.restart_count == 1


async def test_toggle_endpoint_rejects_unknown_server(
    fake_data_dir: Path, admin_password: str
) -> None:
    seed_default_mcp_servers(fake_data_dir / ".hermes" / "config.yaml")
    app = _build_app(gateway=_FakeGateway())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/mcp/toggle", data={"name": "bogus"}
        )
        assert response.status_code == 200
        assert "unknown MCP server" in response.text
