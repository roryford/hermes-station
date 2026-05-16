"""Tests for hermes-webui subprocess env construction.

Critical: the webui subprocess hosts an agent runner that gates tool
visibility on env-var presence (e.g. image_generate requires FAL_KEY).
Before v0.2.2 the env whitelist excluded all secrets, so every credentialed
tool was silently hidden from the model. These tests pin the contract that
Secrets-page keys flow through to the child.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from hermes_station.webui import WebUIProcess, _WEBUI_ENV_PASSTHROUGH


def _process(fake_data_dir: Path) -> WebUIProcess:
    return WebUIProcess(
        webui_src=fake_data_dir / "no-webui",
        hermes_home=fake_data_dir / ".hermes",
        webui_state_dir=fake_data_dir / "webui",
        workspace_dir=fake_data_dir / "workspace",
        config_path=fake_data_dir / ".hermes" / "config.yaml",
    )


def test_static_whitelist_includes_system_keys(fake_data_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("HOME", "/data")
    env = _process(fake_data_dir)._build_env()
    assert env.get("PATH") == "/usr/local/bin:/usr/bin"
    assert env.get("HOME") == "/data"


def test_catalog_secret_forwarded(fake_data_dir: Path, monkeypatch) -> None:
    """A KNOWN_SECRETS key present in os.environ reaches the child env."""
    monkeypatch.setenv("FAL_KEY", "fal-test-12345")
    env = _process(fake_data_dir)._build_env()
    assert env.get("FAL_KEY") == "fal-test-12345"


def test_provider_key_forwarded(fake_data_dir: Path, monkeypatch) -> None:
    """Provider env vars (OPENROUTER_API_KEY, etc.) reach the child."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    env = _process(fake_data_dir)._build_env()
    assert env.get("OPENROUTER_API_KEY") == "sk-or-test"
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-test"


def test_channel_token_forwarded(fake_data_dir: Path, monkeypatch) -> None:
    """Channel tokens (e.g. TELEGRAM_BOT_TOKEN) reach the child."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:abc")
    env = _process(fake_data_dir)._build_env()
    assert env.get("TELEGRAM_BOT_TOKEN") == "12345:abc"


def test_custom_key_from_config_forwarded(fake_data_dir: Path, monkeypatch) -> None:
    """A key tracked via admin.custom_secret_keys reaches the child."""
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"admin": {"custom_secret_keys": ["MY_SERVICE_KEY"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_SERVICE_KEY", "value-1234")

    env = _process(fake_data_dir)._build_env()
    assert env.get("MY_SERVICE_KEY") == "value-1234"


def test_disabled_secret_not_forwarded(fake_data_dir: Path, monkeypatch) -> None:
    """admin.disabled_secrets keys are stripped from the child env.

    Mirrors the lifespan-level pop in seed_env_file_to_os — disabling a
    secret must be effective everywhere, including the webui subprocess.
    """
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"admin": {"disabled_secrets": ["FAL_KEY"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAL_KEY", "should-not-pass-through")

    env = _process(fake_data_dir)._build_env()
    assert "FAL_KEY" not in env


def test_unrelated_env_vars_not_forwarded(fake_data_dir: Path, monkeypatch) -> None:
    """Random env vars that aren't in any catalog stay out of the child env."""
    monkeypatch.setenv("SOME_RANDOM_VAR", "leaked")
    monkeypatch.setenv("MY_DEBUG_FLAG", "1")
    env = _process(fake_data_dir)._build_env()
    assert "SOME_RANDOM_VAR" not in env
    assert "MY_DEBUG_FLAG" not in env


def test_missing_env_var_skipped(fake_data_dir: Path, monkeypatch) -> None:
    """Catalog keys not present in os.environ are quietly skipped (not None'd)."""
    monkeypatch.delenv("FAL_KEY", raising=False)
    env = _process(fake_data_dir)._build_env()
    assert "FAL_KEY" not in env


def test_missing_config_file_does_not_crash(fake_data_dir: Path, monkeypatch) -> None:
    """When config.yaml is absent the helper falls back to catalog-only keys."""
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    if config_path.exists():
        config_path.unlink()
    monkeypatch.setenv("FAL_KEY", "fal-1234")

    env = _process(fake_data_dir)._build_env()
    # Catalog keys still flow even without admin.custom_secret_keys.
    assert env.get("FAL_KEY") == "fal-1234"


def test_static_whitelist_is_minimal(fake_data_dir: Path) -> None:
    """The static whitelist should remain non-secret-aware.

    Bit-rot sentinel: if a future change accidentally adds a credential
    key to the static whitelist, prefer the dynamic Secrets-page path
    instead so the disabled_secrets respect still applies.
    """
    for forbidden in ("FAL_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        assert forbidden not in _WEBUI_ENV_PASSTHROUGH, (
            f"{forbidden} crept into the static whitelist; route it via _secrets_passthrough()"
        )
