"""Tests for hermes_station.admin.provider helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_station.admin.provider import provider_env_var_names, _validate_base_url


# ─────────────────────────────────────────────────────────── provider_env_var_names


def test_provider_env_var_names_anthropic() -> None:
    names = provider_env_var_names("anthropic")
    assert "ANTHROPIC_API_KEY" in names


def test_provider_env_var_names_copilot_accepted_aliases() -> None:
    names = provider_env_var_names("copilot")
    assert "COPILOT_GITHUB_TOKEN" in names
    assert "GH_TOKEN" in names
    assert "GITHUB_TOKEN" in names


def test_provider_env_var_names_unknown_provider() -> None:
    names = provider_env_var_names("bogus")
    assert names == ()


# ─────────────────────────────────────────────────────────── _validate_base_url


def test_validate_base_url_valid_https() -> None:
    result = _validate_base_url("https://api.openai.com/v1")
    assert result == "https://api.openai.com/v1"


def test_validate_base_url_empty_is_ok() -> None:
    assert _validate_base_url("") == ""


def test_validate_base_url_rejects_no_scheme() -> None:
    with pytest.raises(ValueError, match="http or https"):
        _validate_base_url("ftp://example.com/v1")


def test_validate_base_url_rejects_localhost() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _validate_base_url("http://localhost/v1")


def test_validate_base_url_rejects_no_hostname() -> None:
    with pytest.raises(ValueError, match="hostname"):
        _validate_base_url("https:///v1")


# ─────────────────────────────────────────────────────────── provider_has_credentials


def test_provider_has_credentials_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_station.admin.provider import provider_has_credentials

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert provider_has_credentials("anthropic", {}) is True


def test_provider_has_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_station.admin.provider import provider_has_credentials

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider_has_credentials("anthropic", {}) is False


# ─────────────────────────────────────────────────────────── provider_status


def test_provider_status_returns_dict(fake_data_dir: Path) -> None:
    from hermes_station.admin.provider import provider_status

    result = provider_status({}, {})
    assert isinstance(result, dict)
    assert "provider" in result
    assert "ready" in result
