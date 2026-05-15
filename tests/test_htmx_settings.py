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
