"""Unit + light-integration tests for the provider seeder + drift detector.

Covers Track A5 (seeder) + A6 (drift) + C2 (in-process integration) of the
hermes-station DX improvement plan.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from hermes_station.config import (
    DEFAULT_MODELS_BY_PROVIDER,
    PROVIDER_ENV_KEYS,
    Paths,
    detect_provider_drift,
    extract_model_config,
    load_yaml_config,
    seed_provider_from_env,
    write_yaml_config,
)
from hermes_station.readiness import validate_readiness


# -- Seeder: happy paths -----------------------------------------------------


def test_seed_provider_from_env_openrouter(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    result = seed_provider_from_env(cfg, {"OPENROUTER_API_KEY": "sk-or-v1-x"})
    assert result == "openrouter"
    on_disk = load_yaml_config(cfg)
    assert on_disk["model"]["provider"] == "openrouter"
    assert on_disk["model"]["name"] == DEFAULT_MODELS_BY_PROVIDER["openrouter"]


def test_seed_provider_from_env_anthropic(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    result = seed_provider_from_env(cfg, {"ANTHROPIC_API_KEY": "sk-ant-test"})
    assert result == "anthropic"
    on_disk = load_yaml_config(cfg)
    assert on_disk["model"]["provider"] == "anthropic"
    assert on_disk["model"]["name"] == DEFAULT_MODELS_BY_PROVIDER["anthropic"]


def test_seed_provider_from_env_openai(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    result = seed_provider_from_env(cfg, {"OPENAI_API_KEY": "sk-openai-test"})
    assert result == "openai"
    on_disk = load_yaml_config(cfg)
    assert on_disk["model"]["provider"] == "openai"
    assert on_disk["model"]["name"] == DEFAULT_MODELS_BY_PROVIDER["openai"]


# -- Seeder: no-clobber ------------------------------------------------------


def test_seed_provider_from_env_no_clobber(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    write_yaml_config(cfg, {"model": {"provider": "anthropic", "name": "claude-x"}})
    result = seed_provider_from_env(cfg, {"OPENROUTER_API_KEY": "sk-or-v1-x"})
    assert result is None
    on_disk = load_yaml_config(cfg)
    assert on_disk["model"]["provider"] == "anthropic"
    assert on_disk["model"]["name"] == "claude-x"


def test_seed_provider_from_env_partial_model_block_no_clobber(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    # User wrote `model: {name: foo}` with no provider — still treat as
    # user-configured to avoid overwriting their intent.
    write_yaml_config(cfg, {"model": {"name": "foo"}})
    result = seed_provider_from_env(cfg, {"OPENROUTER_API_KEY": "sk-or-v1-x"})
    assert result is None
    on_disk = load_yaml_config(cfg)
    assert on_disk["model"] == {"name": "foo"}


# -- Seeder: precedence + empty-key handling --------------------------------


def test_seed_provider_from_env_precedence(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    result = seed_provider_from_env(
        cfg,
        {
            "OPENROUTER_API_KEY": "sk-or-v1-x",
            "ANTHROPIC_API_KEY": "sk-ant-x",
        },
    )
    # OpenRouter is first in PROVIDER_ENV_KEYS, so it wins.
    assert result == "openrouter"
    assert PROVIDER_ENV_KEYS[0] == ("openrouter", "OPENROUTER_API_KEY")


def test_seed_provider_from_env_no_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    result = seed_provider_from_env(cfg, {})
    assert result is None
    assert not cfg.exists()


def test_seed_provider_ignores_empty_string(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    result = seed_provider_from_env(cfg, {"OPENROUTER_API_KEY": ""})
    assert result is None
    assert not cfg.exists()


def test_seed_provider_ignores_whitespace_only(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    result = seed_provider_from_env(cfg, {"OPENROUTER_API_KEY": "   \t  "})
    assert result is None
    assert not cfg.exists()


# -- Seeder: round-trip + diagnostic logging --------------------------------


def test_seed_provider_roundtrips_through_extract_model_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    seed_provider_from_env(cfg, {"OPENAI_API_KEY": "sk-openai-x"})
    config = load_yaml_config(cfg)
    model = extract_model_config(config)
    assert model.provider == "openai"
    # `extract_model_config` reads `default`, not `name` — the seeder writes
    # `name` (the runtime-facing key); `default` is a separate hermes-agent
    # concept. The roundtrip we care about is provider survival.
    assert config["model"]["name"] == DEFAULT_MODELS_BY_PROVIDER["openai"]


def test_seed_provider_logs_diagnostic_on_skip(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cfg = tmp_path / "config.yaml"

    # Path 1: pre-configured provider.
    write_yaml_config(cfg, {"model": {"provider": "anthropic"}})
    with caplog.at_level(logging.INFO, logger="hermes_station.config"):
        caplog.clear()
        seed_provider_from_env(cfg, {"OPENROUTER_API_KEY": "sk-or-v1-x"})
    assert any("already set to anthropic" in r.message for r in caplog.records)

    # Path 2: nothing in env.
    cfg2 = tmp_path / "config2.yaml"
    with caplog.at_level(logging.INFO, logger="hermes_station.config"):
        caplog.clear()
        seed_provider_from_env(cfg2, {})
    assert any("no recognized provider key in env" in r.message for r in caplog.records)

    # Path 3: env var set but empty.
    cfg3 = tmp_path / "config3.yaml"
    with caplog.at_level(logging.INFO, logger="hermes_station.config"):
        caplog.clear()
        seed_provider_from_env(cfg3, {"OPENROUTER_API_KEY": "   "})
    assert any("present but empty/whitespace" in r.message for r in caplog.records)

    # Path 4: partial model block.
    cfg4 = tmp_path / "config4.yaml"
    write_yaml_config(cfg4, {"model": {"name": "foo"}})
    with caplog.at_level(logging.INFO, logger="hermes_station.config"):
        caplog.clear()
        seed_provider_from_env(cfg4, {"OPENROUTER_API_KEY": "sk-or-v1-x"})
    assert any("model block already present" in r.message for r in caplog.records)


@pytest.mark.manual
def test_default_models_are_currently_supported() -> None:
    """Sentinel against bit-rot — run manually when models retire.

    Until we wire a live provider check, this just asserts every default
    string is non-empty and follows the obvious provider/model shape so a
    typo in `DEFAULT_MODELS_BY_PROVIDER` is caught.
    """
    for provider, model in DEFAULT_MODELS_BY_PROVIDER.items():
        assert model, f"default model for {provider} is empty"
        assert " " not in model


# -- Drift detection ---------------------------------------------------------


def test_detect_provider_drift_seeded_key_removed() -> None:
    config = {"model": {"provider": "openrouter", "name": "x"}}
    env = {"ANTHROPIC_API_KEY": "sk-ant-x"}  # OPENROUTER_API_KEY gone
    msgs = detect_provider_drift(config, env)
    assert len(msgs) == 1
    assert "openrouter" in msgs[0]
    assert "OPENROUTER_API_KEY" in msgs[0]
    assert "ANTHROPIC_API_KEY" in msgs[0]
    assert "/admin/settings" in msgs[0]


def test_detect_provider_drift_no_change_when_key_present() -> None:
    config = {"model": {"provider": "openrouter", "name": "x"}}
    env = {"OPENROUTER_API_KEY": "sk-or-v1-x"}
    assert detect_provider_drift(config, env) == []


def test_detect_provider_drift_emits_no_warning_when_no_alternative() -> None:
    config = {"model": {"provider": "openrouter", "name": "x"}}
    env: dict[str, str] = {}  # no provider keys at all → nothing actionable
    assert detect_provider_drift(config, env) == []


# -- Integration: seeded provider shows up in /health readiness -------------


def test_seeded_provider_appears_in_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C2: seed → load → validate_readiness shows the provider as ready.

    In-process equivalent of the lifespan handoff. The full lifespan is
    exercised end-to-end by `scripts/dx-verify.sh`.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / ".hermes" / "config.yaml"))
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path / "webui"))
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(tmp_path / "workspace"))

    cfg = tmp_path / ".hermes" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    seeded = seed_provider_from_env(cfg, {"OPENROUTER_API_KEY": "sk-or-v1-test"})
    assert seeded == "openrouter"

    config = load_yaml_config(cfg)
    paths = Paths()
    paths.ensure()

    result = validate_readiness(paths, config, {"OPENROUTER_API_KEY": "sk-or-v1-test"})
    row = result.readiness["provider:openrouter"]
    assert row.intended is True
    assert row.ready is True
