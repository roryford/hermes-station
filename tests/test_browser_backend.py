"""Browser backend secrets catalog + MCP seed + DX journey tests.

Covers:
- KNOWN_SECRETS browser group membership (catalog unit tests)
- playwright-mcp URL-based seed/no-clobber (MCP unit tests)
- mcp_status() is_url_based / url fields
- DX journeys: saving browser secrets via the Secrets page HTTP endpoint,
  toggling playwright-mcp via the MCP toggle endpoint.

Mirrors the style, infrastructure, and patterns from test_mcp_default.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.admin.htmx_dashboard import routes as htmx_dashboard_routes
from hermes_station.admin.htmx_settings import routes as htmx_settings_routes
from hermes_station.admin.mcp import mcp_status, toggle_mcp_server
from hermes_station.admin.routes import admin_routes
from hermes_station.admin.secrets_catalog import CATALOG_GROUPS, KNOWN_SECRETS
from hermes_station.config import (
    MCP_SERVER_CATALOG,
    Paths,
    _server_seed_entry,
    load_env_file,
    load_yaml_config,
    seed_default_mcp_servers,
    write_yaml_config,
)


# ---------------------------------------------------------------------------
# Section 1: Secrets catalog unit tests (synchronous)
# ---------------------------------------------------------------------------


def test_browser_group_contains_all_expected_keys() -> None:
    """KNOWN_SECRETS must contain entries for all 5 browser keys, all in group='browser'."""
    expected_browser_keys = {
        "BROWSERBASE_API_KEY",
        "BROWSERBASE_PROJECT_ID",
        "BROWSER_USE_API_KEY",
        "CAMOFOX_URL",
        "STEEL_API_KEY",
    }
    browser_keys = {entry["key"] for entry in KNOWN_SECRETS if entry.get("group") == "browser"}
    assert expected_browser_keys <= browser_keys, (
        f"Missing browser keys: {expected_browser_keys - browser_keys}"
    )
    # Also assert each expected key individually has group="browser"
    key_to_group = {entry["key"]: entry["group"] for entry in KNOWN_SECRETS}
    for key in expected_browser_keys:
        assert key_to_group.get(key) == "browser", f"{key} must have group='browser'"


def test_all_browser_entries_have_required_fields() -> None:
    """For each entry with group='browser': must have key, label, group, hint, in_process=True.
    url field must be a non-empty string."""
    browser_entries = [entry for entry in KNOWN_SECRETS if entry.get("group") == "browser"]
    assert browser_entries, "No browser entries found in KNOWN_SECRETS"
    for entry in browser_entries:
        key = entry["key"]
        assert "key" in entry, f"{key}: missing 'key' field"
        assert "label" in entry, f"{key}: missing 'label' field"
        assert "group" in entry, f"{key}: missing 'group' field"
        assert "hint" in entry, f"{key}: missing 'hint' field"
        assert entry.get("in_process") is True, f"{key}: in_process must be True"
        assert isinstance(entry.get("url"), str) and entry["url"], (
            f"{key}: url must be a non-empty string"
        )


def test_browser_group_in_catalog_groups() -> None:
    assert "browser" in CATALOG_GROUPS


# ---------------------------------------------------------------------------
# Section 2: MCP catalog / seed unit tests (synchronous)
# ---------------------------------------------------------------------------


def test_playwright_mcp_in_catalog() -> None:
    """'playwright-mcp' must be in MCP_SERVER_CATALOG with 'url' but no 'command'/'args'."""
    entry = next((e for e in MCP_SERVER_CATALOG if e["name"] == "playwright-mcp"), None)
    assert entry is not None, "'playwright-mcp' not found in MCP_SERVER_CATALOG"
    assert "url" in entry, "playwright-mcp entry must have 'url' key"
    assert "command" not in entry, "playwright-mcp must not have 'command' key"
    assert "args" not in entry, "playwright-mcp must not have 'args' key"


def test_server_seed_entry_url_based() -> None:
    """_server_seed_entry with a url-based entry returns {url, enabled=False}."""
    seed = _server_seed_entry({"name": "x", "url": "", "env": {}, "needs": []})
    assert seed == {"url": "", "enabled": False}
    assert "command" not in seed
    assert "args" not in seed


def test_server_seed_entry_command_based_unchanged() -> None:
    """_server_seed_entry with a command-based entry returns command/args/enabled."""
    entry = {
        "name": "y",
        "command": "npx",
        "args": ["-y", "some-pkg"],
        "env": {},
        "needs": [],
    }
    seed = _server_seed_entry(entry)
    assert seed["command"] == "npx"
    assert seed["args"] == ["-y", "some-pkg"]
    assert seed["enabled"] is False
    assert "url" not in seed


def test_playwright_mcp_seeded_to_config(tmp_path: Path) -> None:
    """seed_default_mcp_servers writes playwright-mcp as {url: '', enabled: False}."""
    config_path = tmp_path / "config.yaml"
    seed_default_mcp_servers(config_path)
    config = load_yaml_config(config_path)
    assert "playwright-mcp" in config["mcp_servers"]
    entry = config["mcp_servers"]["playwright-mcp"]
    assert entry == {"url": "", "enabled": False}


def test_playwright_mcp_seed_no_clobber(tmp_path: Path) -> None:
    """Pre-existing playwright-mcp url and enabled=True are preserved after re-seed."""
    config_path = tmp_path / "config.yaml"
    write_yaml_config(
        config_path,
        {
            "mcp_servers": {
                "playwright-mcp": {
                    "url": "http://existing.url/mcp",
                    "enabled": True,
                }
            }
        },
    )
    seed_default_mcp_servers(config_path)
    config = load_yaml_config(config_path)
    entry = config["mcp_servers"]["playwright-mcp"]
    assert entry["url"] == "http://existing.url/mcp"
    assert entry["enabled"] is True


def test_mcp_status_url_based_entry() -> None:
    """mcp_status with playwright-mcp url set returns is_url_based=True and correct url."""
    config = {"mcp_servers": {"playwright-mcp": {"url": "http://test/mcp", "enabled": False}}}
    rows = mcp_status(config, {})
    row = next((r for r in rows if r["name"] == "playwright-mcp"), None)
    assert row is not None, "playwright-mcp not found in mcp_status output"
    assert row["is_url_based"] is True
    assert row["url"] == "http://test/mcp"


def test_mcp_status_url_empty_uses_catalog_default() -> None:
    """mcp_status with playwright-mcp url='' returns url='' (from config, not catalog)."""
    config = {"mcp_servers": {"playwright-mcp": {"url": "", "enabled": False}}}
    rows = mcp_status(config, {})
    row = next((r for r in rows if r["name"] == "playwright-mcp"), None)
    assert row is not None
    assert row["url"] == ""


# ---------------------------------------------------------------------------
# Section 3: DX journey tests (async, httpx ASGITransport)
# ---------------------------------------------------------------------------


class _FakeGateway:
    def __init__(self) -> None:
        self.restart_count = 0

    async def restart(self) -> None:
        self.restart_count += 1


def _build_app(gateway: Any | None = None) -> Starlette:
    base_routes: list[Route] = list(htmx_dashboard_routes())
    base_routes.extend(htmx_settings_routes())
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


@pytest.mark.asyncio
async def test_dx_journey_browserbase_cloud(fake_data_dir: Path, admin_password: str) -> None:
    """
    Journey: Developer sets Browserbase keys via Secrets page.
    hermes-agent auto-detects BROWSERBASE_API_KEY + BROWSERBASE_PROJECT_ID → uses Browserbase.
    Verifies both keys written to .env and gateway restarted.
    """
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)

        r1 = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BROWSERBASE_API_KEY", "value": "bb-api-key-abc"},
        )
        assert r1.status_code == 200

        r2 = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BROWSERBASE_PROJECT_ID", "value": "proj-xyz"},
        )
        assert r2.status_code == 200

    env_path = fake_data_dir / ".hermes" / ".env"
    env_values = load_env_file(env_path)
    assert env_values.get("BROWSERBASE_API_KEY") == "bb-api-key-abc"
    assert env_values.get("BROWSERBASE_PROJECT_ID") == "proj-xyz"
    assert gateway.restart_count >= 1


@pytest.mark.asyncio
async def test_dx_journey_browser_use_cloud(fake_data_dir: Path, admin_password: str) -> None:
    """Journey: Developer sets BROWSER_USE_API_KEY → auto-detected by hermes-agent."""
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BROWSER_USE_API_KEY", "value": "bu-key-123"},
        )
        assert r.status_code == 200

    env_path = fake_data_dir / ".hermes" / ".env"
    env_values = load_env_file(env_path)
    assert env_values.get("BROWSER_USE_API_KEY") == "bu-key-123"
    assert gateway.restart_count >= 1


@pytest.mark.asyncio
async def test_dx_journey_steel_cloud(fake_data_dir: Path, admin_password: str) -> None:
    """Journey: Developer sets STEEL_API_KEY (UI ready even before hermes-agent PR ships)."""
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "STEEL_API_KEY", "value": "steel-key-999"},
        )
        assert r.status_code == 200

    env_path = fake_data_dir / ".hermes" / ".env"
    env_values = load_env_file(env_path)
    assert env_values.get("STEEL_API_KEY") == "steel-key-999"
    assert gateway.restart_count >= 1


@pytest.mark.asyncio
async def test_dx_journey_camofox_railway_service(fake_data_dir: Path, admin_password: str) -> None:
    """
    Journey: Developer deploys Camofox to Railway, sets CAMOFOX_URL.
    CAMOFOX_URL takes priority over cloud providers in hermes-agent.
    Verifies CAMOFOX_URL written to .env, gateway restarted.
    """
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "CAMOFOX_URL", "value": "http://camofox.railway.internal:9377"},
        )
        assert r.status_code == 200

    env_path = fake_data_dir / ".hermes" / ".env"
    env_values = load_env_file(env_path)
    assert env_values.get("CAMOFOX_URL") == "http://camofox.railway.internal:9377"
    assert gateway.restart_count >= 1


@pytest.mark.asyncio
async def test_dx_journey_camofox_with_cloud_fallback(fake_data_dir: Path, admin_password: str) -> None:
    """
    Journey: Developer sets both CAMOFOX_URL and BROWSER_USE_API_KEY.
    Both stored independently — Camofox takes priority per hermes-agent priority chain.
    """
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)

        r1 = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "CAMOFOX_URL", "value": "http://camofox.railway.internal:9377"},
        )
        assert r1.status_code == 200

        r2 = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BROWSER_USE_API_KEY", "value": "bu-fallback-key"},
        )
        assert r2.status_code == 200

    env_path = fake_data_dir / ".hermes" / ".env"
    env_values = load_env_file(env_path)
    assert env_values.get("CAMOFOX_URL") == "http://camofox.railway.internal:9377"
    assert env_values.get("BROWSER_USE_API_KEY") == "bu-fallback-key"
    assert gateway.restart_count >= 1


@pytest.mark.asyncio
async def test_dx_journey_disable_browser_backend(fake_data_dir: Path, admin_password: str) -> None:
    """
    Journey: Developer disables a browser backend key.
    Key is added to admin.disabled_secrets in config.yaml.
    """
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)

        # First save the key
        await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BROWSER_USE_API_KEY", "value": "bu-key-to-disable"},
        )

        # Now disable it
        r = await client.post(
            "/admin/_partial/secrets/disable",
            data={"key": "BROWSER_USE_API_KEY"},
        )
        assert r.status_code == 200

    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config = load_yaml_config(config_path)
    admin_block = config.get("admin", {})
    disabled = set(admin_block.get("disabled_secrets", []))
    assert "BROWSER_USE_API_KEY" in disabled


@pytest.mark.asyncio
async def test_dx_journey_playwright_mcp_railway_service(
    fake_data_dir: Path, admin_password: str
) -> None:
    """
    Journey: Playwright MCP as separate Railway service.
    Step 1: playwright-mcp seeded in config.yaml with enabled: false on first boot.
    Step 2: Developer deploys Railway service, updates URL in config.yaml manually.
    Step 3: Developer enables via MCP toggle on dashboard.
    Verifies: seeding correct, toggle flips enabled=True, gateway restarted.
    """
    config_path = fake_data_dir / ".hermes" / "config.yaml"

    # Step 1: seed MCP servers (mimics first boot)
    seed_default_mcp_servers(config_path)

    # Verify playwright-mcp is seeded with enabled=False
    config = load_yaml_config(config_path)
    assert "playwright-mcp" in config["mcp_servers"]
    assert config["mcp_servers"]["playwright-mcp"]["enabled"] is False

    # Step 2: Developer manually updates the URL in config.yaml (simulated here)
    config["mcp_servers"]["playwright-mcp"]["url"] = (
        "http://playwright-mcp.railway.internal:8931/mcp"
    )
    write_yaml_config(config_path, config)

    # Step 3: Developer enables via toggle
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/_partial/mcp/toggle", data={"name": "playwright-mcp"}
        )
        assert r.status_code == 200

    # Verify enabled=True persisted
    config = load_yaml_config(config_path)
    assert config["mcp_servers"]["playwright-mcp"]["enabled"] is True
    # Verify url was preserved
    assert (
        config["mcp_servers"]["playwright-mcp"]["url"]
        == "http://playwright-mcp.railway.internal:8931/mcp"
    )
    assert gateway.restart_count == 1


@pytest.mark.asyncio
async def test_dx_journey_use_railway_value(fake_data_dir: Path, admin_password: str) -> None:
    """
    Journey: Railway env var already set (BROWSERBASE_API_KEY in Railway dashboard).
    Developer uses 'Use Railway' / clear-override flow → key removed from .env.
    Verifies key absent from .env after clearing override.
    """
    gateway = _FakeGateway()
    app = _build_app(gateway=gateway)

    env_path = fake_data_dir / ".hermes" / ".env"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)

        # First set an override
        await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BROWSERBASE_API_KEY", "value": "local-override-key"},
        )
        env_values = load_env_file(env_path)
        assert env_values.get("BROWSERBASE_API_KEY") == "local-override-key"

        # Now clear the override (use Railway value)
        r = await client.post(
            "/admin/_partial/secrets/clear",
            data={"key": "BROWSERBASE_API_KEY"},
        )
        assert r.status_code == 200

    env_values = load_env_file(env_path)
    assert "BROWSERBASE_API_KEY" not in env_values
    assert gateway.restart_count >= 1
