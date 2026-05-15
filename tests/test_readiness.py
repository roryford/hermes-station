"""Unit tests for hermes_station.readiness.validate_readiness."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_station.config import Paths
from hermes_station.readiness import Readiness, validate_readiness


@pytest.fixture
def fake_paths(fake_data_dir: Path) -> Paths:
    return Paths()


def test_validate_readiness_returns_readiness_dataclass(fake_paths: Paths) -> None:
    rd = validate_readiness(fake_paths, {}, {})
    assert isinstance(rd, Readiness)
    assert isinstance(rd.readiness, dict)
    assert rd.versions["hermes_station"]
    assert rd.versions["python"]


def test_default_config_has_no_intended_capabilities(fake_paths: Paths) -> None:
    rd = validate_readiness(fake_paths, {}, {})
    # On a bare config nothing is intended → status should not be "degraded".
    assert rd.any_intended_not_ready() is False


def test_provider_intended_but_missing_credential(fake_paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["provider:anthropic"]
    assert row.intended is True
    assert row.ready is False
    assert "ANTHROPIC_API_KEY" in row.reason
    assert rd.any_intended_not_ready() is True


def test_provider_ready_when_key_present(fake_paths: Paths) -> None:
    config = {"model": {"provider": "anthropic"}}
    rd = validate_readiness(fake_paths, config, {"ANTHROPIC_API_KEY": "sk-xxx"})
    row = rd.readiness["provider:anthropic"]
    assert row.intended is True
    assert row.ready is True
    assert not row.reason


def test_discord_intended_via_messaging_block(fake_paths: Paths) -> None:
    config = {"messaging": {"discord": {"enabled": True}}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["discord"]
    assert row.intended is True
    assert row.ready is False
    assert "DISCORD_BOT_TOKEN" in row.reason


def test_discord_ready_when_token_present(fake_paths: Paths) -> None:
    config = {"messaging": {"discord": {"enabled": True}}}
    rd = validate_readiness(fake_paths, config, {"DISCORD_BOT_TOKEN": "MTAxx"})
    row = rd.readiness["discord"]
    assert row.intended is True
    assert row.ready is True


def test_web_search_intended_with_unknown_backend(fake_paths: Paths) -> None:
    config = {"web": {"search_backend": "foo-engine"}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["web_search"]
    assert row.intended is True
    assert row.ready is False
    assert "foo-engine" in row.reason


def test_web_search_ready_for_brave(fake_paths: Paths) -> None:
    config = {"web": {"search_backend": "brave"}}
    rd = validate_readiness(fake_paths, config, {"BRAVE_API_KEY": "bv-x"})
    row = rd.readiness["web_search"]
    assert row.intended is True
    assert row.ready is True


def test_image_gen_intended_via_toolsets_list(fake_paths: Paths) -> None:
    config = {"toolsets": ["image_gen"]}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["image_gen"]
    assert row.intended is True
    assert row.ready is False
    assert row.reason == "missing FAL_KEY"


def test_image_gen_ready(fake_paths: Paths) -> None:
    config = {"toolsets": ["image_gen"]}
    rd = validate_readiness(fake_paths, config, {"FAL_KEY": "fal-x"})
    assert rd.readiness["image_gen"].ready is True


def test_github_intended_via_mcp_server(fake_paths: Paths) -> None:
    config = {"mcp_servers": {"github": {"enabled": True}}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["github"]
    assert row.intended is True
    assert row.ready is False


def test_github_ready_with_gh_token(fake_paths: Paths) -> None:
    config = {"mcp_servers": {"github": {"enabled": True}}}
    rd = validate_readiness(fake_paths, config, {"GH_TOKEN": "ghp_xxx"})
    assert rd.readiness["github"].ready is True


def test_memory_holographic_ready_when_path_writable(fake_paths: Paths) -> None:
    config = {"memory": {"provider": "holographic"}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["memory:holographic"]
    assert row.intended is True
    assert row.ready is True


def test_unknown_provider_marked_not_ready(fake_paths: Paths) -> None:
    config = {"model": {"provider": "not-a-real-provider"}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["provider:not-a-real-provider"]
    assert row.intended is True
    assert row.ready is False
    assert "unknown provider" in row.reason


def test_placeholder_token_is_not_a_credential(fake_paths: Paths) -> None:
    config = {"model": {"provider": "anthropic"}}
    rd = validate_readiness(fake_paths, config, {"ANTHROPIC_API_KEY": "changeme"})
    assert rd.readiness["provider:anthropic"].ready is False


def test_summary_includes_platforms_and_toolsets(fake_paths: Paths) -> None:
    config = {
        "messaging": {"telegram": {"enabled": True}},
        "toolsets": ["image_gen", "web"],
    }
    rd = validate_readiness(fake_paths, config, {})
    assert "telegram" in rd.summary["platforms"]
    assert "image_gen" in rd.summary["toolsets"]
    assert "web" in rd.summary["toolsets"]
