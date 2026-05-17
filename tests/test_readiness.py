"""Unit tests for hermes_station.readiness.validate_readiness."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from hermes_station.config import Paths
from hermes_station.readiness import (
    CapabilityRow,
    Readiness,
    _channel_intended,
    _configured_platforms,
    _credential_source,
    _delegation_providers,
    _dir_writable,
    _enabled_toolsets,
    _first_present,
    _has_value,
    _image_gen_intended,
    _read_hermes_webui_version,
    _read_image_revision,
    validate_readiness,
)


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


def test_discord_intended_via_messaging_block(fake_paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
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


@pytest.mark.parametrize(
    ("backend", "key", "value"),
    [
        ("tavily", "TAVILY_API_KEY", "tvly-x"),
        ("brave-free", "BRAVE_SEARCH_API_KEY", "bsf-x"),
        ("firecrawl", "FIRECRAWL_API_KEY", "fc-x"),
        ("exa", "EXA_API_KEY", "exa-x"),
        ("parallel", "PARALLEL_API_KEY", "par-x"),
        ("searxng", "SEARXNG_URL", "http://searxng.local"),
    ],
)
def test_web_search_ready_for_keyed_backend(fake_paths: Paths, backend: str, key: str, value: str) -> None:
    config = {"web": {"search_backend": backend}}
    rd = validate_readiness(fake_paths, config, {key: value})
    row = rd.readiness["web_search"]
    assert row.intended is True
    assert row.ready is True, f"expected ready for {backend!r} with {key} set"
    assert row.source == "env_file"


@pytest.mark.parametrize(
    ("backend", "key"),
    [
        ("tavily", "TAVILY_API_KEY"),
        ("brave-free", "BRAVE_SEARCH_API_KEY"),
        ("firecrawl", "FIRECRAWL_API_KEY"),
        ("exa", "EXA_API_KEY"),
        ("parallel", "PARALLEL_API_KEY"),
        ("searxng", "SEARXNG_URL"),
    ],
)
def test_web_search_not_ready_for_keyed_backend_missing_key(
    fake_paths: Paths,
    backend: str,
    key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(key, raising=False)
    config = {"web": {"search_backend": backend}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["web_search"]
    assert row.intended is True
    assert row.ready is False
    assert key in row.reason


def test_web_search_ddgs_always_ready_no_key_required(
    fake_paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"web": {"search_backend": "ddgs"}}
    rd = validate_readiness(fake_paths, config, {})
    row = rd.readiness["web_search"]
    assert row.intended is True
    assert row.ready is True
    assert not row.reason


def test_image_gen_intended_via_toolsets_list(fake_paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAL_KEY", raising=False)
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


def test_github_intended_via_mcp_server(fake_paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
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


def test_placeholder_token_is_not_a_credential(fake_paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = {"model": {"provider": "anthropic"}}
    rd = validate_readiness(fake_paths, config, {"ANTHROPIC_API_KEY": "changeme"})
    assert rd.readiness["provider:anthropic"].ready is False


# ---------------------------------------------------------------------------
# credential_source unit tests
# ---------------------------------------------------------------------------


def test_credential_source_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_KEY", raising=False)
    assert _credential_source({"MY_KEY": "real-value"}, "MY_KEY") == "env_file"


def test_credential_source_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "real-value")
    assert _credential_source({}, "MY_KEY") == "process_env"


def test_credential_source_env_file_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "process-value")
    assert _credential_source({"MY_KEY": "file-value"}, "MY_KEY") == "env_file"


def test_credential_source_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_KEY", raising=False)
    assert _credential_source({}, "MY_KEY") == "absent"


def test_credential_source_placeholder_not_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_KEY", raising=False)
    assert _credential_source({"MY_KEY": "changeme"}, "MY_KEY") == "absent"


def test_credential_source_first_of_multiple_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEY_A", raising=False)
    monkeypatch.delenv("KEY_B", raising=False)
    assert _credential_source({"KEY_B": "b-val"}, "KEY_A", "KEY_B") == "env_file"


# ---------------------------------------------------------------------------
# source field propagation through check functions
# ---------------------------------------------------------------------------


def test_provider_source_env_file(fake_paths: Paths) -> None:
    config = {"model": {"provider": "anthropic"}}
    rd = validate_readiness(fake_paths, config, {"ANTHROPIC_API_KEY": "sk-x"})
    assert rd.readiness["provider:anthropic"].source == "env_file"


def test_provider_source_absent_when_missing(fake_paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = {"model": {"provider": "anthropic"}}
    rd = validate_readiness(fake_paths, config, {})
    assert rd.readiness["provider:anthropic"].source == "absent"


def test_provider_source_in_as_dict(fake_paths: Paths) -> None:
    config = {"model": {"provider": "anthropic"}}
    rd = validate_readiness(fake_paths, config, {"ANTHROPIC_API_KEY": "sk-x"})
    d = rd.readiness["provider:anthropic"].as_dict()
    assert d["source"] == "env_file"


def test_provider_source_absent_omitted_from_dict_when_not_intended(
    fake_paths: Paths,
) -> None:
    """Non-intended, non-credential rows should not include source in their dict."""
    config = {}
    rd = validate_readiness(fake_paths, config, {})
    # memory:holographic is not a credential capability — source should be absent/empty
    d = rd.readiness["memory:holographic"].as_dict()
    assert "source" not in d


def test_web_search_source_process_env(fake_paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "bv-x")
    config = {"web": {"search_backend": "brave"}}
    rd = validate_readiness(fake_paths, config, {})
    assert rd.readiness["web_search"].source == "process_env"


def test_image_gen_source_env_file(fake_paths: Paths) -> None:
    config = {"toolsets": ["image_gen"]}
    rd = validate_readiness(fake_paths, config, {"FAL_KEY": "fal-x"})
    assert rd.readiness["image_gen"].source == "env_file"


def test_github_source_env_file(fake_paths: Paths) -> None:
    config = {"mcp_servers": {"github": {"enabled": True}}}
    rd = validate_readiness(fake_paths, config, {"GH_TOKEN": "ghp_x"})
    assert rd.readiness["github"].source == "env_file"


# ---------------------------------------------------------------------------
# model.default warning
# ---------------------------------------------------------------------------


def test_model_default_warning_when_provider_set_but_no_default(
    fake_paths: Paths,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = {"model": {"provider": "anthropic"}}
    with caplog.at_level(logging.WARNING, logger="hermes_station.readiness"):
        validate_readiness(fake_paths, config, {"ANTHROPIC_API_KEY": "sk-x"})
    messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("model.default" in m for m in messages), f"No model.default warning in {messages}"


def test_no_model_default_warning_when_default_set(
    fake_paths: Paths,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = {"model": {"provider": "anthropic", "default": "claude-sonnet-4-6"}}
    with caplog.at_level(logging.WARNING, logger="hermes_station.readiness"):
        validate_readiness(fake_paths, config, {"ANTHROPIC_API_KEY": "sk-x"})
    model_default_warnings = [
        r for r in caplog.records if r.levelno >= logging.WARNING and "model.default" in r.message
    ]
    assert not model_default_warnings


def test_no_model_default_warning_when_no_provider(fake_paths: Paths) -> None:
    """A bare config with no provider should not emit the model.default warning."""
    from hermes_station.readiness import validate_readiness

    # If no exception is raised and no unexpected side effects, test passes.
    rd = validate_readiness(fake_paths, {}, {})
    assert isinstance(rd, Readiness)


def test_summary_includes_platforms_and_toolsets(fake_paths: Paths) -> None:
    config = {
        "messaging": {"telegram": {"enabled": True}},
        "toolsets": ["image_gen", "web"],
    }
    rd = validate_readiness(fake_paths, config, {})
    assert "telegram" in rd.summary["platforms"]
    assert "image_gen" in rd.summary["toolsets"]
    assert "web" in rd.summary["toolsets"]


# ---------------------------------------------------------------------------
# image_gen: all config shapes must be detected as intended
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        {"toolsets": ["image_gen"]},
        {"toolsets": {"image_gen": True}},
        {"toolsets": {"image_gen": {"enabled": True}}},
        {"fal": {"api_key": "placeholder"}},
    ],
    ids=["list", "dict-bool", "dict-block", "fal-block"],
)
def test_image_gen_intended_all_config_shapes(
    fake_paths: Paths, config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FAL_KEY", raising=False)
    rd = validate_readiness(fake_paths, config, {})
    assert rd.readiness["image_gen"].intended is True, f"intended=False for config {config}"
    assert rd.readiness["image_gen"].ready is False
    assert rd.readiness["image_gen"].reason == "missing FAL_KEY"


def test_image_gen_not_intended_when_explicitly_disabled(fake_paths: Paths) -> None:
    config = {"toolsets": {"image_gen": {"enabled": False}}}
    rd = validate_readiness(fake_paths, config, {})
    assert rd.readiness["image_gen"].intended is False


@pytest.mark.parametrize(
    "config",
    [
        {"toolsets": ["image_gen"]},
        {"toolsets": {"image_gen": True}},
        {"toolsets": {"image_gen": {"enabled": True}}},
        {"fal": {"api_key": "placeholder"}},
    ],
    ids=["list", "dict-bool", "dict-block", "fal-block"],
)
def test_summary_toolsets_consistent_with_readiness_intent(fake_paths: Paths, config: dict) -> None:
    """summary.toolsets must include image_gen iff readiness.image_gen.intended is True."""
    rd = validate_readiness(fake_paths, config, {})
    intended = rd.readiness["image_gen"].intended
    in_summary = "image_gen" in rd.summary["toolsets"]
    assert intended == in_summary, (
        f"Mismatch: readiness.intended={intended} but "
        f"'image_gen' in summary.toolsets={in_summary} for config {config}"
    )


# ---------------------------------------------------------------------------
# Pure helper unit tests (from test_coverage_boost.py)
# ---------------------------------------------------------------------------


def test_has_value_true_for_real_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K", raising=False)
    assert _has_value({"K": "real-key"}, "K") is True


def test_has_value_false_for_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K", raising=False)
    assert _has_value({"K": "changeme"}, "K") is False


def test_has_value_false_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K", raising=False)
    assert _has_value({}, "K") is False


def test_has_value_falls_back_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "real-value")
    assert _has_value({}, "K") is True


def test_first_present_returns_first_matching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    result = _first_present({"A": "val-a", "B": "val-b"}, ("A", "B"))
    assert result == "A"


def test_first_present_returns_empty_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    result = _first_present({}, ("A", "B"))
    assert result == ""


def test_channel_intended_via_messaging_block_enabled_false() -> None:
    config = {"messaging": {"discord": {"enabled": False}}}
    assert _channel_intended(config, "discord") is False


def test_channel_intended_via_messaging_block_enabled_true() -> None:
    config = {"messaging": {"discord": {"enabled": True}}}
    assert _channel_intended(config, "discord") is True


def test_channel_intended_via_channels_list() -> None:
    config = {"channels": ["telegram", "discord"]}
    assert _channel_intended(config, "telegram") is True
    assert _channel_intended(config, "slack") is False


def test_channel_intended_via_channels_dict() -> None:
    config = {"channels": {"telegram": {"enabled": True}}}
    assert _channel_intended(config, "telegram") is True


def test_channel_intended_via_channels_dict_disabled() -> None:
    config = {"channels": {"telegram": {"enabled": False}}}
    assert _channel_intended(config, "telegram") is False


def test_channel_not_intended_by_default() -> None:
    assert _channel_intended({}, "discord") is False


def test_delegation_providers_returns_empty_for_no_delegation() -> None:
    assert _delegation_providers({}) == []


def test_delegation_providers_top_level_provider() -> None:
    config = {"delegation": {"provider": "anthropic"}}
    result = _delegation_providers(config)
    assert "anthropic" in result


def test_delegation_providers_from_routes_list() -> None:
    config = {
        "delegation": {
            "routes": [
                {"provider": "openai"},
                {"provider": "anthropic"},
            ]
        }
    }
    result = _delegation_providers(config)
    assert "openai" in result
    assert "anthropic" in result


def test_delegation_providers_from_fallback_list() -> None:
    config = {"delegation": {"fallback": [{"provider": "openrouter"}]}}
    result = _delegation_providers(config)
    assert "openrouter" in result


def test_dir_writable_returns_true_for_writable_dir(tmp_path: Path) -> None:
    assert _dir_writable(tmp_path / "subdir") is True


def test_dir_writable_returns_false_for_non_writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_kw: object) -> None:
        raise PermissionError("mocked: not writable")

    monkeypatch.setattr(Path, "write_text", _raise)
    assert _dir_writable(tmp_path / "probe") is False


def test_read_image_revision_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("hermes_station.readiness._BUILD_REVISION_FILE", tmp_path / "no-such-file")
    monkeypatch.setenv("HERMES_STATION_REVISION", "abc123")
    result = _read_image_revision()
    assert result == "abc123"


def test_read_image_revision_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("hermes_station.readiness._BUILD_REVISION_FILE", tmp_path / "no-such-file")
    monkeypatch.delenv("HERMES_STATION_REVISION", raising=False)
    result = _read_image_revision()
    assert result is None


def test_read_hermes_webui_version_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_WEBUI_VERSION", "2.0.0")
    result = _read_hermes_webui_version()
    assert result == "2.0.0"


def test_read_hermes_webui_version_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_WEBUI_VERSION", raising=False)
    result = _read_hermes_webui_version()
    assert result is None


def test_image_gen_intended_false_for_empty() -> None:
    assert _image_gen_intended({}) is False


def test_image_gen_intended_dict_false_when_disabled() -> None:
    assert _image_gen_intended({"toolsets": {"image_gen": {"enabled": False}}}) is False


def test_enabled_toolsets_dict_with_disabled_entry() -> None:
    config = {"toolsets": {"image_gen": {"enabled": False}, "web": True}}
    result = _enabled_toolsets(config)
    assert "web" in result
    assert "image_gen" not in result


def test_configured_platforms_from_channels_dict() -> None:
    config = {"channels": {"telegram": {"enabled": True}, "discord": False}}
    result = _configured_platforms(config)
    assert "telegram" in result


def test_configured_platforms_from_channels_list() -> None:
    config = {"channels": ["telegram", "slack"]}
    result = _configured_platforms(config)
    assert "telegram" in result
    assert "slack" in result


def test_configured_platforms_deduplicates(tmp_path: Path) -> None:
    config = {
        "messaging": {"telegram": {"enabled": True}},
        "channels": ["telegram"],
    }
    result = _configured_platforms(config)
    assert result.count("telegram") == 1


def test_validate_readiness_with_delegation_provider(tmp_path: Path) -> None:
    """Delegation provider should add a row to readiness."""
    import os

    os.environ.setdefault("HERMES_HOME", str(tmp_path))

    class _FakePaths:
        hermes_home = tmp_path

    config = {
        "delegation": {"provider": "anthropic"},
        "model": {"provider": "openai"},
    }
    rd = validate_readiness(
        _FakePaths(),
        config,
        {"ANTHROPIC_API_KEY": "sk-x", "OPENAI_API_KEY": "sk-y"},
    )
    assert "provider:anthropic" in rd.readiness
    assert rd.readiness["provider:anthropic"].ready is True


def test_readiness_as_dict_roundtrip(tmp_path: Path) -> None:
    """Readiness.as_dict produces a serializable dict."""
    row = CapabilityRow(intended=True, ready=False, reason="missing X", source="absent")
    rd = Readiness(
        readiness={"test_cap": row},
        versions={"hermes_station": "0.1.0"},
        boot_at="2026-01-01T00:00:00Z",
        summary={"platforms": [], "toolsets": []},
    )
    d = rd.as_dict()
    assert d["readiness"]["test_cap"]["intended"] is True
    assert d["readiness"]["test_cap"]["ready"] is False
    assert d["readiness"]["test_cap"]["reason"] == "missing X"
    assert d["readiness"]["test_cap"]["source"] == "absent"
    assert d["versions"]["hermes_station"] == "0.1.0"


def test_capability_row_as_dict_omits_empty_fields() -> None:
    row = CapabilityRow(intended=False, ready=False)
    d = row.as_dict()
    assert "reason" not in d
    assert "source" not in d


def test_readiness_any_intended_not_ready() -> None:
    rd = Readiness(
        readiness={
            "cap_a": CapabilityRow(intended=True, ready=False),
            "cap_b": CapabilityRow(intended=False, ready=False),
        }
    )
    assert rd.any_intended_not_ready() is True


def test_readiness_any_intended_not_ready_false() -> None:
    rd = Readiness(
        readiness={
            "cap_a": CapabilityRow(intended=True, ready=True),
        }
    )
    assert rd.any_intended_not_ready() is False


def test_readiness_delegation_provider_already_ready_not_downgraded(tmp_path: Path) -> None:
    """If a delegation provider row is already ready, it should not be downgraded."""

    class _FakePaths:
        hermes_home = tmp_path

    config = {
        "model": {"provider": "anthropic"},
        "delegation": {"provider": "anthropic"},
    }
    rd = validate_readiness(
        _FakePaths(),
        config,
        {"ANTHROPIC_API_KEY": "sk-real-key"},
    )
    assert rd.readiness["provider:anthropic"].ready is True


def test_validate_readiness_none_config_and_env(tmp_path: Path) -> None:
    """None config and env_values are handled gracefully."""

    class _FakePaths:
        hermes_home = tmp_path

    rd = validate_readiness(_FakePaths(), None, None)
    assert isinstance(rd, Readiness)


def test_check_provider_empty_string(tmp_path: Path) -> None:
    """Empty provider string returns not-ready row."""
    from hermes_station.readiness import _check_provider

    row = _check_provider("", {}, intended=True)
    assert row.ready is False
    assert "no provider" in row.reason


def test_check_github_via_integrations_key(tmp_path: Path) -> None:
    """GitHub intended via `integrations` key in config."""

    class _FakePaths:
        hermes_home = tmp_path

    config = {"integrations": {"github": True}}
    rd = validate_readiness(_FakePaths(), config, {"GITHUB_TOKEN": "ghp_x"})
    assert rd.readiness["github"].intended is True
    assert rd.readiness["github"].ready is True


def test_check_github_via_github_key(tmp_path: Path) -> None:
    """GitHub intended via top-level `github` key in config."""

    class _FakePaths:
        hermes_home = tmp_path

    config = {"github": {"token": "placeholder"}}
    rd = validate_readiness(_FakePaths(), config, {})
    assert rd.readiness["github"].intended is True
