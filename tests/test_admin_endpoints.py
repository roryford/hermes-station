"""Phase-1 admin endpoint + helper tests.

Covers `hermes_station.admin.{provider,channels,pairing}` and the routes that
expose them. Tests against a fake `/data` tree via the `fake_data_dir` fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from hermes_station.admin import channels, pairing, provider
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


# ────────────────────────────────────────────────────────────── pairing helper


def _write_pairing(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_pairing_approve_moves_pending_to_approved(fake_data_dir: Path) -> None:
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(
        pairing_dir / "telegram-pending.json",
        {"42": {"user_name": "alice", "created_at": 100}},
    )

    pairing.approve(pairing_dir, "42")

    pending = json.loads((pairing_dir / "telegram-pending.json").read_text())
    approved = json.loads((pairing_dir / "telegram-approved.json").read_text())
    assert pending == {}
    assert "42" in approved
    assert approved["42"]["user_name"] == "alice"
    assert "approved_at" in approved["42"]
    assert (pairing_dir / "telegram-approved.json").stat().st_mode & 0o777 == 0o600


def test_pairing_deny_removes_from_pending(fake_data_dir: Path) -> None:
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(
        pairing_dir / "telegram-pending.json",
        {"42": {"user_name": "alice"}, "43": {"user_name": "bob"}},
    )

    pairing.deny(pairing_dir, "42")

    pending = json.loads((pairing_dir / "telegram-pending.json").read_text())
    assert "42" not in pending
    assert "43" in pending


def test_pairing_revoke_removes_from_approved(fake_data_dir: Path) -> None:
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(
        pairing_dir / "telegram-approved.json",
        {"42": {"user_name": "alice", "approved_at": 1}},
    )

    pairing.revoke(pairing_dir, "42")

    approved = json.loads((pairing_dir / "telegram-approved.json").read_text())
    assert approved == {}


def test_pairing_reads_new_path_if_present(fake_data_dir: Path) -> None:
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    new_path = fake_data_dir / ".hermes" / "platforms" / "pairing" / "telegram-approved.json"
    # Legacy path has alice; new path has bob — reader should prefer new.
    _write_pairing(pairing_dir / "telegram-approved.json", {"1": {"user_name": "alice"}})
    _write_pairing(new_path, {"2": {"user_name": "bob"}})

    approved = pairing.get_approved(pairing_dir)
    user_ids = {entry["user_id"] for entry in approved}
    assert user_ids == {"2"}, "new platforms/pairing path should win"


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


async def test_pairing_approve_endpoint(fake_data_dir: Path, admin_password: str) -> None:
    pairing_dir = fake_data_dir / ".hermes" / "pairing"
    _write_pairing(pairing_dir / "telegram-pending.json", {"42": {"user_name": "alice"}})

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)

        resp = await client.post("/admin/api/pairing/approve", json={"user_id": "42"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        approved_resp = await client.get("/admin/api/pairing/approved")
        approved = approved_resp.json()["approved"]
        assert any(entry["user_id"] == "42" for entry in approved)
