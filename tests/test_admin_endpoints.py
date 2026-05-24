"""Phase-1 admin endpoint + helper tests.

Covers `hermes_station.admin.{provider,channels}` and the routes that
expose them. Tests against a fake `/data` tree via the `fake_data_dir` fixture.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from hermes_station.admin import channels, provider
from hermes_station.config import load_env_file


# ─────────────────────────────────────────────────────────── provider helper


def test_apply_provider_setup_anthropic(fake_data_dir: Path) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    env_path = fake_data_dir / ".hermes" / ".env"

    result = provider.apply_provider_setup(
        config_path=config_path,
        env_path=env_path,
        provider="anthropic",
        model="claude-sonnet-4.6",
        api_key="sk-ant-test",
        base_url="",
    )

    assert result == {
        "provider": "anthropic",
        "model": "claude-sonnet-4.6",
        "env_var": "ANTHROPIC_API_KEY",
    }
    config = yaml.safe_load(config_path.read_text())
    assert config["model"]["provider"] == "anthropic"
    assert config["model"]["default"] == "claude-sonnet-4.6"
    assert "base_url" not in config["model"]
    assert load_env_file(env_path)["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_apply_provider_setup_custom_includes_base_url(fake_data_dir: Path) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    env_path = fake_data_dir / ".hermes" / ".env"

    provider.apply_provider_setup(
        config_path=config_path,
        env_path=env_path,
        provider="custom",
        model="gpt-4o-mini",
        api_key="sk-custom",
        base_url="https://example.openai-compat/v1/",
    )

    config = yaml.safe_load(config_path.read_text())
    # Trailing slash is stripped to keep the base URL canonical.
    assert config["model"]["base_url"] == "https://example.openai-compat/v1"
    assert config["model"]["provider"] == "custom"


def test_apply_provider_setup_rejects_unknown_provider(fake_data_dir: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported provider"):
        provider.apply_provider_setup(
            config_path=fake_data_dir / ".hermes" / "config.yaml",
            env_path=fake_data_dir / ".hermes" / ".env",
            provider="bogus",
            model="x",
            api_key="y",
            base_url="",
        )


# ──────────────────────────────────────────────────────────── channels helper


def test_save_channel_values_writes_env_in_sorted_order(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    channels.save_channel_values(
        env_path,
        {
            "TELEGRAM_BOT_TOKEN": "12345:tok",
            "DISCORD_BOT_TOKEN": "discord-tok",
            "SLACK_BOT_TOKEN": "xoxb-slack",
        },
    )
    body = env_path.read_text(encoding="utf-8")
    keys_in_order = [line.split("=", 1)[0] for line in body.strip().splitlines()]
    assert keys_in_order == sorted(keys_in_order)
    assert keys_in_order == ["DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"]


def test_save_channel_values_none_deletes_key(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    channels.save_channel_values(env_path, {"TELEGRAM_BOT_TOKEN": "12345:tok"})
    assert "TELEGRAM_BOT_TOKEN" in load_env_file(env_path)

    channels.save_channel_values(env_path, {"TELEGRAM_BOT_TOKEN": None})
    assert "TELEGRAM_BOT_TOKEN" not in load_env_file(env_path)


def test_channel_status_masks_values() -> None:
    env_values = {
        "TELEGRAM_BOT_TOKEN": "12345:supersecrettoken",
        "TELEGRAM_ALLOWED_USERS": "111,222",
        "WHATSAPP_ENABLED": "true",
    }
    status = {entry["slug"]: entry for entry in channels.channel_status(env_values)}

    assert status["telegram"]["enabled"] is True
    assert "supersecret" not in status["telegram"]["primary_value"]
    # Mask shape: head…tail (head=4, tail=2 per hermes_station.secrets.mask).
    assert status["telegram"]["primary_value"].startswith("1234")
    assert status["telegram"]["primary_value"].endswith("en")

    assert status["whatsapp"]["enabled"] is True
    assert status["discord"]["enabled"] is False


def test_channel_status_disable_key_hides_channel() -> None:
    env_values = {
        "TELEGRAM_BOT_TOKEN": "12345:tok",
        "TELEGRAM_DISABLED": "1",
    }
    status = {entry["slug"]: entry for entry in channels.channel_status(env_values)}
    assert status["telegram"]["enabled"] is False


def test_channel_status_disable_key_absent_keeps_enabled() -> None:
    env_values = {"TELEGRAM_BOT_TOKEN": "12345:tok"}
    status = {entry["slug"]: entry for entry in channels.channel_status(env_values)}
    assert status["telegram"]["enabled"] is True


def test_apply_provider_setup_blank_key_reuses_existing(fake_data_dir: Path) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    env_path = fake_data_dir / ".hermes" / ".env"

    provider.apply_provider_setup(
        config_path=config_path,
        env_path=env_path,
        provider="anthropic",
        model="claude-sonnet-4.6",
        api_key="sk-ant-original",
    )

    # Switching model only — blank api_key should reuse existing
    provider.apply_provider_setup(
        config_path=config_path,
        env_path=env_path,
        provider="anthropic",
        model="claude-opus-4.6",
        api_key="",
    )

    import yaml

    config = yaml.safe_load(config_path.read_text())
    assert config["model"]["default"] == "claude-opus-4.6"
    assert load_env_file(env_path)["ANTHROPIC_API_KEY"] == "sk-ant-original"


def test_apply_provider_setup_blank_key_no_existing_raises(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Clear the env var in case a prior test's seed_env_file_to_os call leaked it
    # into os.environ (seed_env_file_to_os writes directly to os.environ, bypassing
    # monkeypatch cleanup, so it can persist across tests when run in random order).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    env_path = fake_data_dir / ".hermes" / ".env"

    with pytest.raises(ValueError, match="No existing ANTHROPIC_API_KEY"):
        provider.apply_provider_setup(
            config_path=config_path,
            env_path=env_path,
            provider="anthropic",
            model="claude-sonnet-4.6",
            api_key="",
        )


# ─────────────────────────────────────────────────────────────────── endpoints


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


async def test_provider_setup_endpoint_requires_admin(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/api/provider/setup",
            json={"provider": "anthropic", "model": "x", "api_key": "y"},
        )
    assert response.status_code == 401


async def test_channels_save_endpoint_persists(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)

        save = await client.post(
            "/admin/api/channels/save",
            json={"TELEGRAM_BOT_TOKEN": "12345:abc", "TELEGRAM_ALLOWED_USERS": "99"},
        )
        assert save.status_code == 200, save.text
        assert save.json()["ok"] is True

        env_path = fake_data_dir / ".hermes" / ".env"
        env_values = load_env_file(env_path)
        assert env_values["TELEGRAM_BOT_TOKEN"] == "12345:abc"
        assert env_values["TELEGRAM_ALLOWED_USERS"] == "99"

        listing = await client.get("/admin/api/channels")
        assert listing.status_code == 200
        telegram = next(c for c in listing.json()["channels"] if c["slug"] == "telegram")
        assert telegram["enabled"] is True
        assert "abc" not in telegram["primary_value"]


# ─────────────────────────────────────────────── login page no-password message


async def test_login_page_no_password_says_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no admin password is set, login page must say 'locked', not 'disabled'."""
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_PASSWORD", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/login")

    assert resp.status_code == 200
    body = resp.text
    assert "locked" in body.lower(), "login page should say 'locked' when no password is set"
    assert "disabled" not in body.lower(), "login page must not say 'disabled' (implies open access)"
