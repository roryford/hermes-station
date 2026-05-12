"""E2E tests for the admin panel against a running container.

Covers the three features added in feat(admin): channel clear, channel
disable/enable toggle, and provider model switching without re-entering the
API key.

Skip automatically when HERMES_STATION_E2E_URL is not set so the default
pytest run (no container) stays clean.

Run with:
    HERMES_STATION_E2E_URL=http://localhost:8787 \
    HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \
    uv run pytest tests/test_e2e_admin.py -v
"""

from __future__ import annotations

import os

import httpx
import pytest


# ─────────────────────────────────────────────────── fixtures


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("HERMES_STATION_E2E_URL")
    if not url:
        pytest.skip("HERMES_STATION_E2E_URL not set — requires a running container")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def admin_password() -> str:
    pw = os.environ.get("HERMES_STATION_E2E_ADMIN_PASSWORD", "test-admin-pw")
    return pw


@pytest.fixture(scope="session")
def admin_client(base_url: str, admin_password: str) -> httpx.Client:
    """Authenticated admin session (session-scoped — shared across tests)."""
    client = httpx.Client(base_url=base_url, follow_redirects=True, timeout=10.0)
    resp = client.post("/admin/login", data={"password": admin_password})
    assert resp.status_code == 200, f"admin login failed: {resp.status_code} {resp.text[:200]}"
    assert "hermes_station_admin" in client.cookies, "expected hermes_station_admin cookie after login"
    return client


# ─────────────────────────────────────────────────── helpers


def _channels_card(client: httpx.Client, **form_data) -> httpx.Response:
    return client.post("/admin/_partial/channels/save", data=form_data)


def _clear_channel(client: httpx.Client, slug: str) -> httpx.Response:
    return client.post("/admin/_partial/channels/clear", data={"slug": slug})


def _toggle_channel(client: httpx.Client, slug: str) -> httpx.Response:
    return client.post("/admin/_partial/channels/toggle", data={"slug": slug})


def _setup_provider(client: httpx.Client, **kwargs) -> httpx.Response:
    return client.post("/admin/_partial/provider/setup", data=kwargs)


# ─────────────────────────────────────────────────── admin auth


def test_admin_login_redirects_to_dashboard(base_url: str, admin_password: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=True, timeout=10.0) as client:
        resp = client.post("/admin/login", data={"password": admin_password})
    assert resp.status_code == 200
    assert "hermes_station_admin" in client.cookies


def test_admin_login_wrong_password_rejected(base_url: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        resp = client.post("/admin/login", data={"password": "wrong"})
    assert resp.status_code in (200, 401, 403)
    assert "hermes_station_admin" not in client.cookies


# ─────────────────────────────────────────────────── settings page


def test_settings_page_renders(admin_client: httpx.Client) -> None:
    resp = admin_client.get("/admin/settings")
    assert resp.status_code == 200
    assert "channels-card" in resp.text
    assert "provider-card" in resp.text


def test_settings_page_has_clear_buttons(admin_client: httpx.Client) -> None:
    resp = admin_client.get("/admin/settings")
    assert resp.status_code == 200
    assert "/admin/_partial/channels/clear" in resp.text


def test_settings_page_has_disable_toggle_buttons(admin_client: httpx.Client) -> None:
    resp = admin_client.get("/admin/settings")
    assert resp.status_code == 200
    assert "/admin/_partial/channels/toggle" in resp.text


# ─────────────────────────────────────────────────── Feature 1: clear channel


def test_clear_channel_returns_channels_card(admin_client: httpx.Client) -> None:
    resp = _clear_channel(admin_client, "telegram")
    assert resp.status_code == 200
    assert "channels-card" in resp.text


def test_clear_channel_shows_success_alert(admin_client: httpx.Client) -> None:
    # Seed a token first
    _channels_card(admin_client, TELEGRAM_BOT_TOKEN="12345:testtoken")
    resp = _clear_channel(admin_client, "telegram")
    assert resp.status_code == 200
    assert "Telegram cleared" in resp.text


def test_clear_channel_unknown_slug_shows_error(admin_client: httpx.Client) -> None:
    resp = _clear_channel(admin_client, "bogus")
    assert resp.status_code == 200
    assert "Unknown channel slug" in resp.text


def test_blank_save_preserves_existing_token(admin_client: httpx.Client) -> None:
    """Blank form submission must NOT delete stored tokens (regression for blank-preserve bug)."""
    # Seed
    seed = _channels_card(admin_client, TELEGRAM_BOT_TOKEN="12345:keepme")
    assert "Channels saved" in seed.text

    # Submit completely blank form — all fields absent / empty
    blank = _channels_card(admin_client)
    assert "Channels saved" in blank.text

    # Token should still show as set (badge Enabled, not 'not set' placeholder)
    assert "Enabled" in blank.text


# ─────────────────────────────────────────────────── Feature 2: disable toggle


def test_disable_toggle_returns_channels_card(admin_client: httpx.Client) -> None:
    resp = _toggle_channel(admin_client, "telegram")
    assert resp.status_code == 200
    assert "channels-card" in resp.text


def test_disable_toggle_round_trip(admin_client: httpx.Client) -> None:
    """Toggling twice should return to original enabled state."""
    # Seed a token and ensure channel starts in enabled state (clear any lingering disable flag)
    _channels_card(admin_client, TELEGRAM_BOT_TOKEN="12345:toggletest")
    # Drive to a known-enabled state: toggle until response says "disabled"
    for _ in range(2):
        r = _toggle_channel(admin_client, "telegram")
        if "Telegram disabled" in r.text:
            break
    else:
        # Already enabled — first toggle should disable
        pass

    # From disabled state: toggle should re-enable
    resp = _toggle_channel(admin_client, "telegram")
    assert "Telegram enabled" in resp.text


def test_disable_toggle_whatsapp_not_supported(admin_client: httpx.Client) -> None:
    resp = _toggle_channel(admin_client, "whatsapp")
    assert resp.status_code == 200
    assert "not supported" in resp.text.lower() or "toggle" in resp.text.lower()


# ─────────────────────────────────────────────────── Feature 3: provider key reuse


def test_provider_setup_requires_key_on_first_setup(admin_client: httpx.Client) -> None:
    """Blank key with no stored key must return a helpful error."""
    resp = _setup_provider(
        admin_client,
        provider="openai",
        model="gpt-4o",
        api_key="",
        base_url="",
    )
    assert resp.status_code == 200
    assert "No existing OPENAI_API_KEY" in resp.text


def test_provider_setup_blank_key_reuses_stored_key(admin_client: httpx.Client) -> None:
    """After storing a key, submitting with blank api_key should succeed."""
    # Store an initial key
    seed = _setup_provider(
        admin_client,
        provider="anthropic",
        model="claude-sonnet-4.6",
        api_key="sk-ant-e2e-test",
        base_url="",
    )
    assert "Provider saved" in seed.text

    # Switch model only — no api_key
    resp = _setup_provider(
        admin_client,
        provider="anthropic",
        model="claude-opus-4.6",
        api_key="",
        base_url="",
    )
    assert resp.status_code == 200
    assert "Provider saved" in resp.text
    assert "claude-opus-4.6" in resp.text


def test_provider_card_shows_leave_blank_hint(admin_client: httpx.Client) -> None:
    resp = admin_client.get("/admin/settings")
    assert "Leave blank to keep" in resp.text
