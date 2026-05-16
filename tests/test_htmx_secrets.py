"""HTMX integration tests for the Secrets card.

Exercises the /admin/_partial/secrets/* endpoints through httpx's ASGI
transport. Mirrors the structure of test_htmx_settings.py for channels.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml
from starlette.applications import Starlette

from hermes_station.admin.htmx_settings import routes as htmx_routes
from hermes_station.admin.routes import admin_routes
from hermes_station.admin.secrets_catalog import KNOWN_SECRETS
from hermes_station.config import Paths, load_env_file, load_yaml_config


@pytest.fixture(autouse=True)
def _scrub_secret_env_vars() -> Iterator[None]:
    """The endpoints under test call seed_env_file_to_os, which does an
    untracked os.environ.update — so any value written during a test leaks
    into later tests. Snapshot every catalog key plus a few common custom
    names before each test, restore after.
    """
    keys = [entry["key"] for entry in KNOWN_SECRETS] + ["MY_CUSTOM"]
    snapshot = {k: os.environ.get(k) for k in keys}
    try:
        yield
    finally:
        for k, v in snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _build_app() -> Starlette:
    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    # Snapshot the current env as a stand-in for the lifespan-set boot_environ,
    # so shadow detection works in test apps that don't run lifespan.
    app.state.boot_environ = dict(os.environ)
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


# ---------------------------------------------------------------------------
# Settings page renders the secrets card
# ---------------------------------------------------------------------------


async def test_settings_page_includes_secrets_card(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/settings")
    assert response.status_code == 200
    body = response.text
    assert 'id="secrets-card"' in body
    assert "FAL_KEY" in body
    assert "Add custom secret" in body
    # Inline help for the sandbox toggle.
    assert "sandboxed tools" in body


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


async def test_secrets_save_requires_auth(fake_data_dir: Path) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "FAL_KEY", "value": "v"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_secrets_disable_requires_auth(fake_data_dir: Path) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/disable",
            data={"key": "FAL_KEY"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Round-trip: save → clear → disable → enable
# ---------------------------------------------------------------------------


async def test_secrets_save_persists(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "FAL_KEY", "value": "fal-test-1234"},
        )
    assert resp.status_code == 200
    assert "FAL_KEY saved." in resp.text
    env_path = fake_data_dir / ".hermes" / ".env"
    assert load_env_file(env_path)["FAL_KEY"] == "fal-test-1234"


async def test_secrets_save_rejects_newline(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "FAL_KEY", "value": "v\nevil"},
        )
    assert resp.status_code == 200
    assert "newline" in resp.text


async def test_secrets_clear_removes_override(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "FAL_KEY", "value": "v"},
        )
        resp = await client.post(
            "/admin/_partial/secrets/clear",
            data={"key": "FAL_KEY"},
        )
    assert resp.status_code == 200
    assert "cleared." in resp.text
    env_path = fake_data_dir / ".hermes" / ".env"
    assert "FAL_KEY" not in load_env_file(env_path)


async def test_secrets_disable_writes_config(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/disable",
            data={"key": "FAL_KEY"},
        )
    assert resp.status_code == 200
    assert "disabled" in resp.text
    config = yaml.safe_load((fake_data_dir / ".hermes" / "config.yaml").read_text())
    assert "FAL_KEY" in config["admin"]["disabled_secrets"]


async def test_secrets_enable_round_trip(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        await client.post("/admin/_partial/secrets/disable", data={"key": "FAL_KEY"})
        resp = await client.post("/admin/_partial/secrets/enable", data={"key": "FAL_KEY"})
    assert resp.status_code == 200
    assert "re-enabled" in resp.text
    config = load_yaml_config(fake_data_dir / ".hermes" / "config.yaml")
    assert "FAL_KEY" not in (config.get("admin", {}).get("disabled_secrets") or [])


# ---------------------------------------------------------------------------
# Add custom secret (with and without sandbox passthrough)
# ---------------------------------------------------------------------------


async def test_secrets_add_custom_key_no_value(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_CUSTOM"},
        )
    assert resp.status_code == 200
    assert "MY_CUSTOM added." in resp.text
    config = load_yaml_config(fake_data_dir / ".hermes" / "config.yaml")
    assert "MY_CUSTOM" in (config.get("admin", {}).get("custom_secret_keys") or [])
    # No value → no env entry.
    env_path = fake_data_dir / ".hermes" / ".env"
    assert "MY_CUSTOM" not in load_env_file(env_path)


async def test_secrets_add_custom_with_value_saves(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_CUSTOM", "value": "val-1234"},
        )
    assert resp.status_code == 200
    assert "added and saved" in resp.text
    env_path = fake_data_dir / ".hermes" / ".env"
    assert load_env_file(env_path)["MY_CUSTOM"] == "val-1234"


async def test_secrets_add_with_sandbox_updates_passthrough(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_CUSTOM", "value": "v", "sandbox": "1"},
        )
    assert resp.status_code == 200
    config = load_yaml_config(fake_data_dir / ".hermes" / "config.yaml")
    passthrough = config.get("terminal", {}).get("env_passthrough", [])
    assert "MY_CUSTOM" in passthrough


async def test_secrets_add_without_sandbox_does_not_passthrough(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_CUSTOM", "value": "v"},
        )
    config = load_yaml_config(fake_data_dir / ".hermes" / "config.yaml")
    passthrough = config.get("terminal", {}).get("env_passthrough", [])
    assert "MY_CUSTOM" not in passthrough


async def test_secrets_add_rejects_bad_key_name(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "lower-case", "value": "v"},
        )
    assert resp.status_code == 200
    assert "invalid key" in resp.text


async def test_secrets_forget_removes_custom_key(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_CUSTOM", "value": "v"},
        )
        resp = await client.post(
            "/admin/_partial/secrets/forget",
            data={"key": "MY_CUSTOM"},
        )
    assert resp.status_code == 200
    assert "forgotten." in resp.text
    config = load_yaml_config(fake_data_dir / ".hermes" / "config.yaml")
    assert "MY_CUSTOM" not in (config.get("admin", {}).get("custom_secret_keys") or [])
    env_path = fake_data_dir / ".hermes" / ".env"
    assert "MY_CUSTOM" not in load_env_file(env_path)


# ---------------------------------------------------------------------------
# Card surfaces shadow warning when override differs from environ
# ---------------------------------------------------------------------------


async def test_secrets_card_renders_shadow_warning(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """When .env override differs from Railway-set env, the card warns."""
    # Simulate Railway-set FAL_KEY.
    monkeypatch.setenv("FAL_KEY", "railway-value-12345")
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        # Save an override that differs from the Railway value.
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "FAL_KEY", "value": "override-value-abcd"},
        )
    assert resp.status_code == 200
    assert "Railway also sets" in resp.text
