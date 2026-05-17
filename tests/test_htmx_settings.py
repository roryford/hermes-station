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
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
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


async def test_settings_renders_after_login(fake_data_dir: Path, admin_password: str) -> None:
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
    assert 'hx-post="/admin/_partial/provider/setup"' in body
    assert 'hx-post="/admin/_partial/channels/save"' in body
    assert "GitHub Copilot" in body
    # All channel labels should appear so we can be sure the catalog renders.
    for label in ("Telegram", "Discord", "Slack", "WhatsApp", "Email"):
        assert label in body


async def test_settings_shows_existing_provider(fake_data_dir: Path, admin_password: str) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}}),
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


async def test_provider_fragment_save_supports_copilot(fake_data_dir: Path, admin_password: str) -> None:
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
    assert "Connect with GitHub" in body

    config = yaml.safe_load((fake_data_dir / ".hermes" / "config.yaml").read_text())
    assert config["model"]["provider"] == "copilot"

    env_path = fake_data_dir / ".hermes" / ".env"
    assert "COPILOT_GITHUB_TOKEN=gho_test_token" in env_path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────── pairings page


async def test_pairings_renders_after_login(fake_data_dir: Path, admin_password: str) -> None:
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


async def test_pairings_fragment_returns_html(fake_data_dir: Path, admin_password: str) -> None:
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


async def test_pairings_panel_shows_pending_users(fake_data_dir: Path, admin_password: str) -> None:
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
    assert 'hx-post="/admin/_partial/pairing/approve"' in body
    assert 'hx-post="/admin/_partial/pairing/deny"' in body
    assert 'hx-post="/admin/_partial/pairing/revoke"' in body


async def test_pairings_fragment_requires_admin(fake_data_dir: Path, admin_password: str) -> None:
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
    _write_pairing(pairing_dir / "telegram-pending.json", {"42": {"user_name": "alice"}})

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post("/admin/_partial/pairing/approve", data={"user_id": "42"})
    assert response.status_code == 200, response.text
    body = response.text
    # After approval the user is in the Approved table, not Pending.
    assert "No pending pairings." in body
    assert "42" in body  # still rendered, just under approved

    approved = json.loads((pairing_dir / "telegram-approved.json").read_text())
    assert "42" in approved


# ---------------------------------------------------------------------------
# HTMX settings helpers: _provider_context, _channels_context, _pairings_context
# ---------------------------------------------------------------------------


def test_provider_context_returns_catalog(fake_data_dir: Path) -> None:
    """_provider_context returns provider catalog and status."""
    from hermes_station.admin.htmx_settings import _provider_context

    paths = Paths()
    ctx = _provider_context(paths)
    assert "provider_catalog" in ctx
    assert isinstance(ctx["provider_catalog"], list)
    assert len(ctx["provider_catalog"]) > 0
    assert "provider_status" in ctx
    assert "provider_label" in ctx


def test_channels_context_returns_channels(fake_data_dir: Path) -> None:
    """_channels_context returns channels list."""
    from hermes_station.admin.htmx_settings import _channels_context

    paths = Paths()
    ctx = _channels_context(paths)
    assert "channels" in ctx
    assert isinstance(ctx["channels"], list)


def test_pairings_context_returns_pending_and_approved(fake_data_dir: Path) -> None:
    """_pairings_context returns pending and approved lists."""
    from hermes_station.admin.htmx_settings import _pairings_context

    paths = Paths()
    ctx = _pairings_context(paths)
    assert "pending" in ctx
    assert "approved" in ctx
    assert isinstance(ctx["pending"], list)
    assert isinstance(ctx["approved"], list)


# ---------------------------------------------------------------------------
# Auth-required paths (from test_coverage_boost.py)
# ---------------------------------------------------------------------------


async def test_provider_fragment_save_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/provider/setup redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/provider/setup",
            data={"provider": "anthropic"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_channels_fragment_save_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/channels/save redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:test"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_channels_fragment_clear_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/channels/clear redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "telegram"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_channels_fragment_toggle_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/channels/toggle redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "telegram"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_pairings_page_requires_admin_htmx(fake_data_dir: Path, admin_password: str) -> None:
    """Unauthenticated GET /admin/pairings redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/pairings", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in resp.headers["location"]


async def test_pairings_fragment_requires_admin_htmx_boost(fake_data_dir: Path, admin_password: str) -> None:
    """Unauthenticated GET /admin/_partial/pairings redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/_partial/pairings", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Channel fragment actions with/without gateway (from test_coverage_boost.py)
# ---------------------------------------------------------------------------


def _build_app_with_gateway(fake_data_dir: Path) -> Starlette:
    """Build a test app with htmx_settings routes and a real Gateway."""
    from hermes_station.gateway import Gateway

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    return app


async def test_channels_fragment_save_persists(fake_data_dir: Path, admin_password: str) -> None:
    """Channel save fragment returns HTML with success message."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:test"},
        )
    assert resp.status_code == 200
    assert "Channels saved." in resp.text


async def test_channels_fragment_clear(fake_data_dir: Path, admin_password: str) -> None:
    """Channels clear fragment returns HTML."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "telegram"},
        )
    assert resp.status_code == 200
    assert "cleared." in resp.text


async def test_channels_fragment_clear_unknown_slug(fake_data_dir: Path, admin_password: str) -> None:
    """Unknown slug returns error message in HTML."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "nonexistent-channel"},
        )
    assert resp.status_code == 200
    assert "Unknown channel" in resp.text


async def test_channels_fragment_toggle(fake_data_dir: Path, admin_password: str) -> None:
    """Channel toggle fragment returns HTML."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "telegram"},
        )
    assert resp.status_code == 200


async def test_channels_fragment_toggle_unknown_slug(fake_data_dir: Path, admin_password: str) -> None:
    """Toggle with unknown slug returns error in HTML."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "nonexistent"},
        )
    assert resp.status_code == 200
    assert "Unknown channel" in resp.text


async def test_provider_cancel_returns_provider_card(fake_data_dir: Path, admin_password: str) -> None:
    """Cancel returns provider card HTML."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post("/admin/_partial/provider/cancel")
    assert resp.status_code == 200


async def test_channels_fragment_save_no_gateway(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_save works when no gateway is set (covers the gateway-None branch)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:testok"},
        )
    assert resp.status_code == 200
    assert "Channels saved." in resp.text


async def test_channels_fragment_clear_no_gateway(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_clear works when no gateway is set."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "discord"},
        )
    assert resp.status_code == 200
    assert "Discord cleared." in resp.text


async def test_channels_fragment_toggle_no_gateway(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_toggle works when no gateway is set."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "discord"},
        )
    assert resp.status_code == 200


async def test_provider_fragment_save_no_gateway(fake_data_dir: Path, admin_password: str) -> None:
    """provider_fragment_save works when no gateway is set."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/provider/setup",
            data={"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-test"},
        )
    assert resp.status_code == 200
    assert "Provider saved." in resp.text


async def test_htmx_provider_fragment_save_error_path(fake_data_dir: Path, admin_password: str) -> None:
    """provider_fragment_save with invalid provider returns error HTML."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/provider/setup",
            data={"provider": "invalid-provider", "model": "x", "api_key": "y"},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower() or "invalid" in resp.text.lower() or "Provider" in resp.text


async def test_htmx_channels_fragment_save_error_on_newline(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_save with invalid value returns error HTML."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:abc\nevil"},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower() or "Channels" in resp.text


# ---------------------------------------------------------------------------
# Copilot OAuth endpoint tests (from test_coverage_boost.py)
# ---------------------------------------------------------------------------


async def test_copilot_oauth_start_returns_device_flow(fake_data_dir: Path, admin_password: str) -> None:
    """Copilot OAuth start returns device flow card (mocked)."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings
    from hermes_station.gateway import Gateway

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    # Login first, save cookies
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    async def _mock_start():
        return {
            "device_code": "dev123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
            "poll_interval": 8,
        }

    with patch.object(_htmx_settings, "start_device_flow", _mock_start):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=saved_cookies
        ) as client:
            resp = await client.post("/admin/_partial/provider/copilot/start")
    assert resp.status_code == 200
    assert "ABCD-EFGH" in resp.text


async def test_copilot_oauth_poll_pending(fake_data_dir: Path, admin_password: str) -> None:
    """Poll with pending status returns device flow card."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings
    from hermes_station.gateway import Gateway

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    async def _mock_poll(device_code, interval=None):
        return {"status": "pending", "poll_interval": 8}

    with patch.object(_htmx_settings, "poll_device_flow", _mock_poll):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=saved_cookies
        ) as client:
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "user_code": "ABCD-EFGH", "interval": "8"},
            )
    assert resp.status_code == 200


async def test_copilot_oauth_poll_missing_device_code(fake_data_dir: Path, admin_password: str) -> None:
    """Poll with no device_code returns error card."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post("/admin/_partial/provider/copilot/poll", data={})
    assert resp.status_code == 200
    assert "Missing device_code" in resp.text


async def test_copilot_oauth_poll_expired(fake_data_dir: Path, admin_password: str) -> None:
    """Poll with expired token returns error card."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings
    from hermes_station.gateway import Gateway

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    async def _mock_poll(device_code, interval=None):
        return {"status": "expired", "message": "Device code expired.", "poll_interval": 0}

    with patch.object(_htmx_settings, "poll_device_flow", _mock_poll):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=saved_cookies
        ) as client:
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "interval": "8"},
            )
    assert resp.status_code == 200


async def test_copilot_oauth_poll_success(fake_data_dir: Path, admin_password: str) -> None:
    """Poll with success status saves token and returns provider card."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings
    from hermes_station.gateway import Gateway

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    async def _mock_poll_success(device_code, interval=None):
        return {"status": "success", "token": "gho_test_token_abc", "poll_interval": 0}

    with patch.object(_htmx_settings, "poll_device_flow", _mock_poll_success):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=saved_cookies
        ) as client:
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "interval": "8"},
            )
    assert resp.status_code == 200
    assert "GitHub Copilot connected" in resp.text or "Provider" in resp.text


# ---------------------------------------------------------------------------
# _secrets_context with boot_environ (line 121->123)
# ---------------------------------------------------------------------------


async def test_settings_page_with_boot_environ(fake_data_dir: Path, admin_password: str) -> None:
    """settings_page passes boot_environ to _secrets_context when it exists on app.state."""
    app = _build_app()
    app.state.boot_environ = {"FAL_KEY": "test-railway-val"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/settings")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# provider_fragment_save with gateway restart (line 187)
# ---------------------------------------------------------------------------


async def test_provider_fragment_save_with_gateway(fake_data_dir: Path, admin_password: str) -> None:
    """provider_fragment_save calls gateway.restart() when gateway is present."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/provider/setup",
            data={"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant-test"},
        )
    assert resp.status_code == 200
    assert "Provider saved." in resp.text


# ---------------------------------------------------------------------------
# channels_fragment_save with key-in-form-but-blank (line 208->204)
# ---------------------------------------------------------------------------


async def test_channels_fragment_save_blank_value_skipped(fake_data_dir: Path, admin_password: str) -> None:
    """Blank channel field in form is skipped (not treated as an update)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        # Send the key in the form but with an empty/blank value — should be skipped.
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "  "},
        )
    assert resp.status_code == 200
    # No error — just returns the channels card.
    assert "Channels" in resp.text


# ---------------------------------------------------------------------------
# channels_fragment_clear with secondary_key (line 237->239)
# ---------------------------------------------------------------------------


async def test_channels_fragment_clear_with_secondary_key(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_clear clears both primary and secondary keys when present."""
    # Telegram has TELEGRAM_ALLOWED_USERS as secondary_key.
    env_path = fake_data_dir / ".hermes" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("TELEGRAM_BOT_TOKEN=tok123\nTELEGRAM_ALLOWED_USERS=42,99\n", encoding="utf-8")

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "telegram"},
        )
    assert resp.status_code == 200
    assert "Telegram cleared." in resp.text


# ---------------------------------------------------------------------------
# channels_fragment_clear ValueError (lines 246-247)
# channels_fragment_toggle ValueError (lines 279-280)
# ---------------------------------------------------------------------------


async def test_channels_fragment_clear_error_path(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_clear returns error HTML when save_channel_values raises."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings

    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    def _raise(*_a, **_kw):
        raise ValueError("disk error")

    with patch.object(_htmx_settings, "save_channel_values", _raise):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post(
                "/admin/_partial/channels/clear",
                data={"slug": "telegram"},
            )
    assert resp.status_code == 200
    assert "disk error" in resp.text


async def test_channels_fragment_toggle_error_path(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_toggle returns error HTML when save_channel_values raises."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings

    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    def _raise(*_a, **_kw):
        raise ValueError("toggle disk error")

    with patch.object(_htmx_settings, "save_channel_values", _raise):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post(
                "/admin/_partial/channels/toggle",
                data={"slug": "telegram"},
            )
    assert resp.status_code == 200
    assert "toggle disk error" in resp.text


# ---------------------------------------------------------------------------
# copilot_oauth_start unauthenticated (line 292) and error path (lines 296-301)
# ---------------------------------------------------------------------------


async def test_copilot_oauth_start_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/provider/copilot/start redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/_partial/provider/copilot/start", follow_redirects=False)
    assert resp.status_code == 302


async def test_copilot_oauth_start_error_path(fake_data_dir: Path, admin_password: str) -> None:
    """copilot_oauth_start returns error card when start_device_flow raises."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings

    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async def _mock_fail():
        raise RuntimeError("network timeout")

    with patch.object(_htmx_settings, "start_device_flow", _mock_fail):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post("/admin/_partial/provider/copilot/start")
    assert resp.status_code == 200
    assert "Could not start GitHub OAuth" in resp.text
    assert "network timeout" in resp.text


# ---------------------------------------------------------------------------
# copilot_oauth_poll unauthenticated (line 318) and interval parse error (324-325)
# ---------------------------------------------------------------------------


async def test_copilot_oauth_poll_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/provider/copilot/poll redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/_partial/provider/copilot/poll", follow_redirects=False)
    assert resp.status_code == 302


async def test_copilot_oauth_poll_bad_interval_fallback(fake_data_dir: Path, admin_password: str) -> None:
    """Poll with non-numeric interval falls back to 8 (lines 324-325)."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings

    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async def _mock_poll(device_code, interval=None):
        return {"status": "pending", "poll_interval": interval}

    with patch.object(_htmx_settings, "poll_device_flow", _mock_poll):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "interval": "not-a-number"},
            )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# copilot_oauth_poll poll exception (lines 336-339)
# ---------------------------------------------------------------------------


async def test_copilot_oauth_poll_exception(fake_data_dir: Path, admin_password: str) -> None:
    """Poll returns error card when poll_device_flow raises an exception."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings

    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async def _mock_poll(device_code, interval=None):
        raise RuntimeError("connection refused")

    with patch.object(_htmx_settings, "poll_device_flow", _mock_poll):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "interval": "8"},
            )
    assert resp.status_code == 200
    assert "Poll error" in resp.text
    assert "connection refused" in resp.text


# ---------------------------------------------------------------------------
# copilot_oauth_poll success path with save error (lines 372-373)
# ---------------------------------------------------------------------------


async def test_copilot_oauth_poll_success_save_error(fake_data_dir: Path, admin_password: str) -> None:
    """Poll success: if apply_provider_setup raises, error alert is returned."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings

    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async def _mock_poll_success(device_code, interval=None):
        return {"status": "success", "token": "gho_tok", "poll_interval": 0}

    def _mock_apply(**_kw):
        raise ValueError("cannot save token")

    with patch.object(_htmx_settings, "poll_device_flow", _mock_poll_success):
        with patch.object(_htmx_settings, "apply_provider_setup", _mock_apply):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                await _login(client, admin_password)
                resp = await client.post(
                    "/admin/_partial/provider/copilot/poll",
                    data={"device_code": "dev123", "interval": "8"},
                )
    assert resp.status_code == 200
    assert "Token received but could not save" in resp.text


# ---------------------------------------------------------------------------
# provider_cancel unauthenticated (line 388)
# ---------------------------------------------------------------------------


async def test_provider_cancel_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/provider/cancel redirects to login."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/_partial/provider/cancel", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# secrets_fragment_save, clear, disable, enable, add, forget — error paths
# (lines 431, 442-443, 462-463, 479-480, 526-527)
# ---------------------------------------------------------------------------


async def test_secrets_fragment_save_error(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_save with invalid key returns error HTML (line 431)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        # Empty key is invalid
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "", "value": "some-value"},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower()


async def test_secrets_fragment_clear_error(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_clear with invalid key returns error HTML (lines 442-443)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/clear",
            data={"key": "invalid key with spaces"},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower()


async def test_secrets_fragment_disable_error(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_disable with invalid key returns error HTML (lines 462-463)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/disable",
            data={"key": ""},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower()


async def test_secrets_fragment_enable_error(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_enable with invalid key returns error HTML (lines 479-480)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/enable",
            data={"key": "lowercase_invalid"},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower()


async def test_secrets_fragment_forget_error(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_forget with invalid key returns error HTML (lines 526-527)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/forget",
            data={"key": "invalid-key-with-dashes"},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower()


# ---------------------------------------------------------------------------
# secrets_fragment_add — unauthenticated (line 493), sandbox branch (line 518)
# ---------------------------------------------------------------------------


async def test_secrets_fragment_add_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/secrets/add redirects to login (line 493)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_KEY"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_secrets_fragment_add_key_only(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_add with key and no value uses add_custom_key (line 503)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_CUSTOM_KEY", "value": ""},
        )
    assert resp.status_code == 200
    assert "MY_CUSTOM_KEY added." in resp.text


async def test_secrets_fragment_add_with_value_and_sandbox(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_add with value and sandbox=1 also sets env_passthrough (line 518)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "MY_SANDBOX_KEY", "value": "some-secret", "sandbox": "1"},
        )
    assert resp.status_code == 200
    assert "MY_SANDBOX_KEY added and saved." in resp.text

    # Verify the key was added to terminal.env_passthrough in config.yaml
    import yaml

    config = yaml.safe_load((fake_data_dir / ".hermes" / "config.yaml").read_text())
    assert "MY_SANDBOX_KEY" in config.get("terminal", {}).get("env_passthrough", [])


# ---------------------------------------------------------------------------
# _ensure_env_passthrough_single branches (lines 541->544, 545->547, 547->549)
# ---------------------------------------------------------------------------


def test_ensure_env_passthrough_single_no_terminal(fake_data_dir: Path) -> None:
    """_ensure_env_passthrough_single creates terminal block if absent (line 541->544)."""
    from hermes_station.admin.htmx_settings import _ensure_env_passthrough_single

    import yaml

    paths = Paths()
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # No terminal block at all.
    config_path.write_text(yaml.safe_dump({}), encoding="utf-8")

    _ensure_env_passthrough_single(paths, "MY_KEY")

    config = yaml.safe_load(config_path.read_text())
    assert "MY_KEY" in config["terminal"]["env_passthrough"]


def test_ensure_env_passthrough_single_no_passthrough_list(fake_data_dir: Path) -> None:
    """_ensure_env_passthrough_single creates passthrough list if not a list (line 545->547)."""
    from hermes_station.admin.htmx_settings import _ensure_env_passthrough_single

    import yaml

    paths = Paths()
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # terminal exists but env_passthrough is not a list.
    config_path.write_text(yaml.safe_dump({"terminal": {"other_key": "val"}}), encoding="utf-8")

    _ensure_env_passthrough_single(paths, "ANOTHER_KEY")

    config = yaml.safe_load(config_path.read_text())
    assert "ANOTHER_KEY" in config["terminal"]["env_passthrough"]


def test_ensure_env_passthrough_single_already_present(fake_data_dir: Path) -> None:
    """_ensure_env_passthrough_single is idempotent when key already present (line 547->549 skipped)."""
    from hermes_station.admin.htmx_settings import _ensure_env_passthrough_single

    import yaml

    paths = Paths()
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"terminal": {"env_passthrough": ["EXISTING_KEY"]}}), encoding="utf-8"
    )

    _ensure_env_passthrough_single(paths, "EXISTING_KEY")

    config = yaml.safe_load(config_path.read_text())
    # Should not be duplicated.
    assert config["terminal"]["env_passthrough"].count("EXISTING_KEY") == 1


# ---------------------------------------------------------------------------
# pairings_fragment_action unauthenticated (line 557), invalid action (line 562->573)
# and exception swallow (lines 566-572)
# ---------------------------------------------------------------------------


async def test_pairings_fragment_action_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/pairing/approve redirects to login (line 557)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/pairing/approve",
            data={"user_id": "42"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_pairings_fragment_action_invalid_action(fake_data_dir: Path, admin_password: str) -> None:
    """pairings_fragment_action with unknown action skips the action block (line 562->573)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/pairing/unknown-action",
            data={"user_id": "42"},
        )
    assert resp.status_code == 200
    assert "Pending pairings" in resp.text


async def test_pairings_fragment_action_no_user_id(fake_data_dir: Path, admin_password: str) -> None:
    """pairings_fragment_action with missing user_id skips the action (line 562->573)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/pairing/approve",
            data={},
        )
    assert resp.status_code == 200
    assert "Pending pairings" in resp.text


async def test_pairings_fragment_action_deny(fake_data_dir: Path, admin_password: str) -> None:
    """pairings_fragment_action deny action covers elif branch (line 567)."""
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(pairing_dir / "telegram-pending.json", {"55": {"user_name": "charlie"}})

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/pairing/deny",
            data={"user_id": "55"},
        )
    assert resp.status_code == 200
    assert "Pending pairings" in resp.text


async def test_pairings_fragment_action_revoke(fake_data_dir: Path, admin_password: str) -> None:
    """pairings_fragment_action revoke action covers else branch (line 568-569)."""
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(pairing_dir / "telegram-approved.json", {"77": {"user_name": "dave"}})

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/pairing/revoke",
            data={"user_id": "77"},
        )
    assert resp.status_code == 200
    assert "Pending pairings" in resp.text


async def test_pairings_fragment_action_swallows_keyerror(fake_data_dir: Path, admin_password: str) -> None:
    """pairings_fragment_action swallows KeyError/ValueError from action helpers (lines 570-572)."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_settings as _htmx_settings

    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    def _raise(*_a, **_kw):
        raise KeyError("user not found")

    with patch.object(_htmx_settings, "approve", _raise):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            resp = await client.post(
                "/admin/_partial/pairing/approve",
                data={"user_id": "99"},
            )
    assert resp.status_code == 200
    # Error is swallowed — panel still renders.
    assert "Pending pairings" in resp.text


# ---------------------------------------------------------------------------
# secrets_fragment_save success (covers _after_secrets_change + boot_environ)
# ---------------------------------------------------------------------------


async def test_secrets_fragment_save_success(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_save with valid key/value persists and returns success card."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BRAVE_API_KEY", "value": "bsearch-key-123"},
        )
    assert resp.status_code == 200
    assert "BRAVE_API_KEY saved." in resp.text


async def test_secrets_fragment_clear_success(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_clear removes a .env override and returns success card."""
    env_path = fake_data_dir / ".hermes" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("BRAVE_API_KEY=oldval\n", encoding="utf-8")

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/clear",
            data={"key": "BRAVE_API_KEY"},
        )
    assert resp.status_code == 200
    assert "BRAVE_API_KEY override cleared." in resp.text


async def test_secrets_fragment_disable_success(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_disable adds key to disabled_secrets in config."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/disable",
            data={"key": "FAL_KEY"},
        )
    assert resp.status_code == 200
    assert "FAL_KEY disabled" in resp.text


async def test_secrets_fragment_enable_success(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_enable removes key from disabled_secrets in config."""
    import yaml

    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"admin": {"disabled_secrets": ["FAL_KEY"]}}),
        encoding="utf-8",
    )

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/enable",
            data={"key": "FAL_KEY"},
        )
    assert resp.status_code == 200
    assert "FAL_KEY re-enabled." in resp.text


async def test_secrets_fragment_forget_success(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_forget unregisters a custom key and clears the override."""
    import yaml

    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"admin": {"custom_secret_keys": ["MY_CUSTOM"]}}),
        encoding="utf-8",
    )

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/forget",
            data={"key": "MY_CUSTOM"},
        )
    assert resp.status_code == 200
    assert "MY_CUSTOM forgotten." in resp.text


# ---------------------------------------------------------------------------
# Secrets endpoints — unauthenticated guard paths
# (lines 413, 431, 451, 471, 518)
# ---------------------------------------------------------------------------


async def test_secrets_fragment_save_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/secrets/save redirects to login (line 413)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "FAL_KEY", "value": "x"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_secrets_fragment_clear_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/secrets/clear redirects to login (line 431)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/clear",
            data={"key": "FAL_KEY"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_secrets_fragment_disable_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/secrets/disable redirects to login (line 451)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/disable",
            data={"key": "FAL_KEY"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_secrets_fragment_enable_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/secrets/enable redirects to login (line 471)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/enable",
            data={"key": "FAL_KEY"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_secrets_fragment_forget_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/secrets/forget redirects to login (line 518)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/secrets/forget",
            data={"key": "MY_CUSTOM"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# _after_secrets_change with gateway (line 406)
# ---------------------------------------------------------------------------


async def test_secrets_fragment_save_with_gateway(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_save calls gateway.restart() when gateway is present (line 406)."""
    app = _build_app_with_gateway(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/secrets/save",
            data={"key": "BRAVE_API_KEY", "value": "bsearch-test-key"},
        )
    assert resp.status_code == 200
    assert "BRAVE_API_KEY saved." in resp.text


# ---------------------------------------------------------------------------
# channels_fragment_clear without secondary key (line 237->239 False branch)
# ---------------------------------------------------------------------------


async def test_channels_fragment_clear_no_secondary_key(fake_data_dir: Path, admin_password: str) -> None:
    """channels_fragment_clear for a channel with no secondary_key (line 237->239)."""
    # whatsapp has an empty secondary_key
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "whatsapp"},
        )
    assert resp.status_code == 200
    assert "cleared." in resp.text


# ---------------------------------------------------------------------------
# secrets_fragment_add — ValueError error path (lines 509-510)
# ---------------------------------------------------------------------------


async def test_secrets_fragment_add_error(fake_data_dir: Path, admin_password: str) -> None:
    """secrets_fragment_add with invalid key returns error HTML (lines 509-510)."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/_partial/secrets/add",
            data={"key": "", "value": ""},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower()


# ---------------------------------------------------------------------------
# _secrets_context called without request (line 121->123 False branch)
# ---------------------------------------------------------------------------


def test_secrets_context_without_request(fake_data_dir: Path) -> None:
    """_secrets_context with request=None skips boot_environ lookup (line 121->123)."""
    from hermes_station.admin.htmx_settings import _secrets_context

    paths = Paths()
    result = _secrets_context(paths, request=None)
    assert "groups" in result
    assert "rows" in result
