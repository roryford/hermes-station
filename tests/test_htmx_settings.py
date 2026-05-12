"""HTMX admin pages — settings and pairings.

The production `create_app()` does not yet wire the htmx page routes (that's a
later integration step owned by a sibling worker). To keep these tests
self-contained, we build a minimal Starlette app from `admin_routes()` plus
`htmx_settings.routes()` and exercise it through httpx's ASGI transport.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import yaml
from starlette.applications import Starlette

from hermes_station.admin.htmx_settings import routes as htmx_routes
from hermes_station.admin.routes import admin_routes
from hermes_station.config import Paths


def _build_app() -> Starlette:
    """Test-only app: real admin routes + htmx routes, no proxy or supervisors."""
    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post(
        "/admin/login", data={"password": password}, follow_redirects=False
    )
    assert response.status_code == 302, response.text


def _write_pairing(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ──────────────────────────────────────────────────────────── settings page


async def test_settings_requires_admin(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/settings", follow_redirects=False)
    # Unauthed GET on a non-/admin/api/ path redirects to login.
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


async def test_settings_renders_after_login(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/settings")
    assert response.status_code == 200
    body = response.text
    assert "Provider" in body
    assert "Channels" in body
    # Provider + channels forms are present.
    assert "hx-post=\"/admin/_partial/provider/setup\"" in body
    assert "hx-post=\"/admin/_partial/channels/save\"" in body
    assert "GitHub Copilot" in body
    # All channel labels should appear so we can be sure the catalog renders.
    for label in ("Telegram", "Discord", "Slack", "WhatsApp", "Email"):
        assert label in body


async def test_settings_shows_existing_provider(
    fake_data_dir: Path, admin_password: str
) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(
            {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}}
        ),
        encoding="utf-8",
    )

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/settings")
    assert response.status_code == 200
    body = response.text
    assert "Anthropic" in body
    assert "claude-sonnet-4.6" in body


async def test_provider_fragment_save_persists_and_returns_card(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/provider/setup",
            data={
                "provider": "anthropic",
                "model": "claude-sonnet-4.6",
                "api_key": "sk-ant-test",
                "base_url": "",
            },
        )
    assert response.status_code == 200, response.text
    body = response.text
    assert "Provider saved." in body
    # The refreshed card should reflect the new state.
    assert "Anthropic" in body

    config = yaml.safe_load((fake_data_dir / ".hermes" / "config.yaml").read_text())
    assert config["model"]["provider"] == "anthropic"


async def test_provider_fragment_save_supports_copilot(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/provider/setup",
            data={
                "provider": "copilot",
                "model": "gpt-4.1",
                "api_key": "gho_test_token",
                "base_url": "",
            },
        )
    assert response.status_code == 200, response.text
    body = response.text
    assert "Provider saved." in body
    assert "GitHub Copilot" in body
    assert "GitHub token" in body
    assert "Classic ghp_* PATs are not supported." in body

    config = yaml.safe_load((fake_data_dir / ".hermes" / "config.yaml").read_text())
    assert config["model"]["provider"] == "copilot"

    env_path = fake_data_dir / ".hermes" / ".env"
    assert "COPILOT_GITHUB_TOKEN=gho_test_token" in env_path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────── pairings page


async def test_pairings_renders_after_login(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/pairings")
    assert response.status_code == 200
    body = response.text
    # The page is a shell — text comes from the fragment, but the shell itself
    # must wire HTMX up to load the fragment.
    assert "pairings-panel" in body
    assert "/admin/_partial/pairings" in body


async def test_pairings_fragment_returns_html(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/pairings")
    assert response.status_code == 200
    body = response.text
    assert "Pending pairings" in body
    assert "Approved users" in body
    # No pending or approved seeded — empty state messages should be shown.
    assert "No pending pairings." in body
    assert "No approved users." in body


async def test_pairings_panel_shows_pending_users(
    fake_data_dir: Path, admin_password: str
) -> None:
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(
        pairing_dir / "telegram-pending.json",
        {"42": {"user_name": "alice", "created_at": 100}},
    )
    _write_pairing(
        pairing_dir / "telegram-approved.json",
        {"7": {"user_name": "bob", "approved_at": 50}},
    )

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/pairings")
    assert response.status_code == 200
    body = response.text
    # Pending row.
    assert "42" in body
    assert "alice" in body
    # Approved row.
    assert "7" in body
    assert "bob" in body
    # Approve/Deny/Revoke buttons rendered for the right rows.
    assert "hx-post=\"/admin/_partial/pairing/approve\"" in body
    assert "hx-post=\"/admin/_partial/pairing/deny\"" in body
    assert "hx-post=\"/admin/_partial/pairing/revoke\"" in body


async def test_pairings_fragment_requires_admin(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/_partial/pairings", follow_redirects=False)
    # No /admin/api/ prefix, so this redirects to login rather than returning 401.
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


async def test_pairings_approve_fragment_moves_and_returns_panel(
    fake_data_dir: Path, admin_password: str
) -> None:
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(
        pairing_dir / "telegram-pending.json", {"42": {"user_name": "alice"}}
    )

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/pairing/approve", data={"user_id": "42"}
        )
    assert response.status_code == 200, response.text
    body = response.text
    # After approval the user is in the Approved table, not Pending.
    assert "No pending pairings." in body
    assert "42" in body  # still rendered, just under approved

    approved = json.loads((pairing_dir / "telegram-approved.json").read_text())
    assert "42" in approved
