"""Compatibility tests — the executable form of docs/CONTRACT.md.

When this file and CONTRACT.md disagree, this file wins.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from hermes_station import secrets
from hermes_station.config import (
    Paths,
    extract_model_config,
    load_env_file,
    load_yaml_config,
    write_env_file,
    write_yaml_config,
)


# ───────────────────────────────────────────────────── file-format contract


def test_env_file_format_roundtrip(tmp_path: Path) -> None:
    """CONTRACT.md §4.1: dotenv, KEY=VALUE per line, sorted, mode 0600."""
    path = tmp_path / ".env"
    write_env_file(path, {"TELEGRAM_BOT_TOKEN": "12345:abc", "ANTHROPIC_API_KEY": "sk-xyz"})

    body = path.read_text(encoding="utf-8")
    assert body == "ANTHROPIC_API_KEY=sk-xyz\nTELEGRAM_BOT_TOKEN=12345:abc\n", "sorted, KEY=VALUE per line"
    assert path.stat().st_mode & 0o777 == 0o600, "mode 0600"

    assert load_env_file(path) == {
        "ANTHROPIC_API_KEY": "sk-xyz",
        "TELEGRAM_BOT_TOKEN": "12345:abc",
    }


def test_env_file_reader_tolerates_legacy_format(tmp_path: Path) -> None:
    """Accept blank lines, comments, and surrounding quotes — hermes-all-in-one's reader behavior."""
    path = tmp_path / ".env"
    path.write_text(
        "# legacy comment\n"
        "\n"
        'ANTHROPIC_API_KEY="sk-xyz"\n'
        "TELEGRAM_BOT_TOKEN='12345:abc'\n"
        "  PADDED_KEY  =  padded-value  \n"
    )
    assert load_env_file(path) == {
        "ANTHROPIC_API_KEY": "sk-xyz",
        "TELEGRAM_BOT_TOKEN": "12345:abc",
        "PADDED_KEY": "padded-value",
    }


def test_yaml_config_roundtrip(tmp_path: Path) -> None:
    """CONTRACT.md §4.2: a config.yaml that hermes-agent reads byte-for-byte unchanged."""
    path = tmp_path / "config.yaml"
    data = {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}}
    write_yaml_config(path, data)

    parsed = yaml.safe_load(path.read_text())
    assert parsed == data, "structure preserved"
    assert path.stat().st_mode & 0o777 == 0o600

    # extract_model_config returns the documented fields
    model = extract_model_config(load_yaml_config(path))
    assert model.provider == "anthropic"
    assert model.default == "claude-sonnet-4.6"
    assert model.base_url == ""


def test_yaml_config_with_custom_provider_base_url(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    write_yaml_config(
        path,
        {"model": {"provider": "custom", "default": "gpt-4o-mini", "base_url": "https://ollama.cloud/v1"}},
    )
    model = extract_model_config(load_yaml_config(path))
    assert model.provider == "custom"
    assert model.base_url == "https://ollama.cloud/v1"


# ─────────────────────────────────────────────────────────── paths contract


def test_paths_honor_env_vars(fake_data_dir: Path) -> None:
    """CONTRACT.md §2.2: every documented env var overrides its default."""
    paths = Paths()
    assert paths.hermes_home == fake_data_dir / ".hermes"
    assert paths.config_path == fake_data_dir / ".hermes" / "config.yaml"
    assert paths.env_path == fake_data_dir / ".hermes" / ".env"
    assert paths.webui_state_dir == fake_data_dir / "webui"
    assert paths.workspace_dir == fake_data_dir / "workspace"


def test_paths_ensure_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / ".hermes" / "config.yaml"))
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path / "webui"))
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(tmp_path / "workspace"))

    paths = Paths()
    paths.ensure()
    paths.ensure()  # second call must not error
    assert paths.hermes_home.exists()
    assert paths.webui_state_dir.exists()
    assert paths.workspace_dir.exists()


# ─────────────────────────────────────────────────────────── secret layering


def test_secrets_env_wins_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    write_env_file(env_path, {"ANTHROPIC_API_KEY": "from-file"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")

    resolved = secrets.resolve_many(["ANTHROPIC_API_KEY"], env_path)["ANTHROPIC_API_KEY"]
    assert resolved.value == "from-env"
    assert resolved.source == "env"


def test_secrets_file_used_when_env_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    write_env_file(env_path, {"ANTHROPIC_API_KEY": "from-file"})

    resolved = secrets.resolve_many(["ANTHROPIC_API_KEY"], env_path)["ANTHROPIC_API_KEY"]
    assert resolved.value == "from-file"
    assert resolved.source == "file"


def test_secrets_unset_when_nothing_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEVER_SET_KEY", raising=False)
    resolved = secrets.resolve_many(["NEVER_SET_KEY"], tmp_path / ".env")["NEVER_SET_KEY"]
    assert resolved.value is None
    assert resolved.source == "unset"


# ──────────────────────────────────────────────────── admin /status contract


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


async def test_status_endpoint_paths(fake_data_dir: Path, admin_password: str) -> None:
    """CONTRACT.md §5.1: /admin/api/status returns the documented `paths` block."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/api/status")
        assert response.status_code == 200
        body = response.json()

    assert body["paths"] == {
        "hermes_home": str(fake_data_dir / ".hermes"),
        "config_path": str(fake_data_dir / ".hermes" / "config.yaml"),
        "env_path": str(fake_data_dir / ".hermes" / ".env"),
        "webui_state_dir": str(fake_data_dir / "webui"),
        "workspace_dir": str(fake_data_dir / "workspace"),
    }
    assert body["model"] == {"provider": "", "default": "", "base_url": ""}, "no provider configured yet"
    assert body["phase"] == "1"
    assert body["webui"]["running"] is False, "webui supervisor not started in unit-test lifespan"
    assert body["gateway"]["state"] == "unknown"


async def test_status_picks_up_existing_config(fake_data_dir: Path, admin_password: str) -> None:
    """An existing /data volume's config.yaml shows up in the status response unchanged."""
    write_yaml_config(
        fake_data_dir / ".hermes" / "config.yaml",
        {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}},
    )

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/api/status")
        body = response.json()

    assert body["model"]["provider"] == "anthropic"
    assert body["model"]["default"] == "claude-sonnet-4.6"


async def test_status_requires_admin(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/api/status")
        assert response.status_code == 401


async def test_admin_login_rejects_wrong_password(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/login",
            data={"password": "wrong-password"},
            follow_redirects=False,
        )
        assert response.status_code == 401


async def test_health_is_always_open(fake_data_dir: Path) -> None:
    """Healthcheck never requires auth — Railway needs it to probe."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.text == "ok"
