"""Unit tests for should_autostart auto mode — provider-only requirement (no channel needed).

These supplement the tests in test_supervisors.py with focused edge-case coverage
for the updated auto mode semantics.
"""

from __future__ import annotations

import pytest

from hermes_station.gateway import should_autostart


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ambient provider keys from leaking into tests."""
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "COPILOT_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_auto_provider_only_returns_true() -> None:
    """Provider configured with key, no channel — True (the DX fix)."""
    config = {"model": {"provider": "anthropic"}}
    env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
    assert should_autostart(mode="auto", config=config, env_values=env) is True


def test_auto_no_provider_returns_false() -> None:
    """No provider in config — False."""
    assert should_autostart(mode="auto", config={}, env_values={}) is False


def test_auto_empty_provider_string_returns_false() -> None:
    """provider: '' is treated as not configured."""
    config = {"model": {"provider": ""}}
    env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
    assert should_autostart(mode="auto", config=config, env_values=env) is False


def test_auto_none_provider_returns_false() -> None:
    """provider: null is treated as not configured."""
    config = {"model": {"provider": None}}
    env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
    assert should_autostart(mode="auto", config=config, env_values=env) is False


def test_auto_provider_no_credentials_returns_false() -> None:
    """Provider is in catalog but no API key is set — False."""
    config = {"model": {"provider": "openrouter"}}
    assert should_autostart(mode="auto", config=config, env_values={}) is False


def test_auto_provider_plus_channel_still_true() -> None:
    """Regression guard: provider + channel still returns True."""
    config = {"model": {"provider": "anthropic"}}
    env = {"ANTHROPIC_API_KEY": "sk-ant-test", "TELEGRAM_BOT_TOKEN": "12345:abc"}
    assert should_autostart(mode="auto", config=config, env_values=env) is True


def test_auto_provider_key_from_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider key in os.environ (not env_values) is accepted."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-railway")
    config = {"model": {"provider": "openrouter"}}
    assert should_autostart(mode="auto", config=config, env_values={}) is True


def test_auto_unknown_provider_returns_false() -> None:
    """Provider not in catalog — False."""
    config = {"model": {"provider": "nonexistent-provider"}}
    env = {"NONEXISTENT_KEY": "some-value"}
    assert should_autostart(mode="auto", config=config, env_values=env) is False


def test_force_on_always_true() -> None:
    """mode=1 always returns True regardless of config."""
    assert should_autostart(mode="1", config={}, env_values={}) is True
    assert should_autostart(mode="true", config={}, env_values={}) is True


def test_force_off_always_false() -> None:
    """mode=0 always returns False regardless of config."""
    config = {"model": {"provider": "anthropic"}}
    env = {"ANTHROPIC_API_KEY": "sk-ant-test", "TELEGRAM_BOT_TOKEN": "tok"}
    assert should_autostart(mode="0", config=config, env_values=env) is False
    assert should_autostart(mode="false", config=config, env_values=env) is False
