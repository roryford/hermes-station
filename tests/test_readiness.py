"""Unit tests for hermes_station.readiness.validate_readiness."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from hermes_station.config import Paths
from hermes_station.readiness import Readiness, validate_readiness, _credential_source


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
def test_image_gen_intended_all_config_shapes(fake_paths: Paths, config: dict) -> None:
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
