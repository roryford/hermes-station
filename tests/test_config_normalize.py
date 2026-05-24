"""Tests for config normalization + the broadened first-boot seeders.

`normalize_config` heals known shape-drift (string env_passthrough,
stray top-level env_passthrough). The new seeders mirror the no-clobber
contract documented in CONTRACT.md §3.3.
"""

from __future__ import annotations

from pathlib import Path

from hermes_station.config import (
    apply_first_boot_seeds,
    heal_mcp_server_launchers,
    load_yaml_config,
    normalize_config,
    seed_neutral_personality_default,
    seed_show_cost_default,
    write_yaml_config,
)


# ---------------------------------------------------------------------------
# normalize_config
# ---------------------------------------------------------------------------


def test_normalize_coerces_string_env_passthrough_to_list() -> None:
    config = {"terminal": {"env_passthrough": "FOO, BAR ,BAZ"}}

    out, changes = normalize_config(config)

    assert out["terminal"]["env_passthrough"] == ["FOO", "BAR", "BAZ"]
    assert len(changes) == 1
    assert "env_passthrough" in changes[0]


def test_normalize_drops_blank_top_level_env_passthrough() -> None:
    config = {"env_passthrough": None, "terminal": {"env_passthrough": ["A"]}}

    out, changes = normalize_config(config)

    assert "env_passthrough" not in out
    assert out["terminal"]["env_passthrough"] == ["A"]
    assert any("blank top-level" in c for c in changes)


def test_normalize_is_idempotent_on_dirty_config() -> None:
    config = {
        "env_passthrough": "",
        "terminal": {"env_passthrough": "X,Y"},
    }
    _, first = normalize_config(config)
    _, second = normalize_config(config)

    assert first  # something changed
    assert second == []


def test_normalize_preserves_well_formed_config() -> None:
    config = {
        "terminal": {"env_passthrough": ["GITHUB_TOKEN", "GH_TOKEN"]},
        "model": {"provider": "anthropic"},
    }
    snapshot = {
        "terminal": {"env_passthrough": ["GITHUB_TOKEN", "GH_TOKEN"]},
        "model": {"provider": "anthropic"},
    }

    out, changes = normalize_config(config)

    assert changes == []
    assert out == snapshot


# ---------------------------------------------------------------------------
# heal_mcp_server_launchers
# ---------------------------------------------------------------------------


def test_heal_rewrites_legacy_filesystem_npx_launcher() -> None:
    config = {
        "mcp_servers": {
            "filesystem": {
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem@2025.8.21",
                    "/data/workspace",
                ],
                "enabled": True,
            }
        }
    }

    changes = heal_mcp_server_launchers(config)

    assert len(changes) == 1
    fs = config["mcp_servers"]["filesystem"]
    assert fs["command"] == "mcp-server-filesystem"
    assert fs["args"] == ["/data/workspace"]
    # Other keys preserved.
    assert fs["enabled"] is True


def test_heal_rewrites_legacy_github_npx_launcher() -> None:
    config = {
        "mcp_servers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"},
                "enabled": False,
            }
        }
    }

    changes = heal_mcp_server_launchers(config)

    assert len(changes) == 1
    gh = config["mcp_servers"]["github"]
    assert gh["command"] == "mcp-server-github"
    assert gh["args"] == []
    assert gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "${GITHUB_TOKEN}"
    assert gh["enabled"] is False


def test_heal_rewrites_legacy_fetch_uvx_launcher() -> None:
    config = {
        "mcp_servers": {
            "fetch": {
                "command": "uvx",
                "args": ["--from", "mcp-server-fetch==2025.4.7", "mcp-server-fetch"],
                "enabled": True,
            }
        }
    }

    changes = heal_mcp_server_launchers(config)

    assert len(changes) == 1
    fetch = config["mcp_servers"]["fetch"]
    assert fetch["command"] == "mcp-server-fetch"
    assert fetch["args"] == []
    assert fetch["enabled"] is True


def test_heal_leaves_user_customizations_alone() -> None:
    """A hand-edited entry that doesn't match the prior-seed shape must
    survive unchanged — better to leave operator overrides than silently
    rewrite them."""
    config = {
        "mcp_servers": {
            "filesystem": {
                # Custom command — operator chose this deliberately.
                "command": "node",
                "args": ["/opt/my-fork/server.js", "/data/workspace"],
                "enabled": True,
            },
            "github": {
                # npx-launched but unpinned — not the prior seed shape.
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "enabled": True,
            },
        }
    }
    snapshot = {k: dict(v) for k, v in config["mcp_servers"].items()}

    changes = heal_mcp_server_launchers(config)

    assert changes == []
    assert config["mcp_servers"] == snapshot


def test_heal_is_idempotent() -> None:
    config = {
        "mcp_servers": {
            "filesystem": {
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem@2025.8.21",
                    "/data/workspace",
                ],
                "enabled": False,
            }
        }
    }

    first = heal_mcp_server_launchers(config)
    second = heal_mcp_server_launchers(config)

    assert len(first) == 1
    assert second == []


def test_heal_no_mcp_servers_block_is_noop() -> None:
    config = {"model": {"provider": "anthropic"}}
    assert heal_mcp_server_launchers(config) == []


def test_normalize_runs_heal_mcp_step() -> None:
    """heal_mcp_server_launchers must be invoked through normalize_config so
    the on-disk lifespan path (which calls normalize_config) picks it up."""
    config = {
        "mcp_servers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"],
                "enabled": True,
            }
        }
    }

    _, changes = normalize_config(config)

    assert any("github" in c and "mcp-server-github" in c for c in changes)
    assert config["mcp_servers"]["github"]["command"] == "mcp-server-github"


# ---------------------------------------------------------------------------
# seed_neutral_personality_default
# ---------------------------------------------------------------------------


def test_personality_seed_writes_default_when_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    assert not config_path.exists()

    wrote = seed_neutral_personality_default(config_path)

    assert wrote is True
    config = load_yaml_config(config_path)
    assert config["display"]["personality"] == "default"


def test_personality_seed_writes_when_display_block_present_but_personality_absent(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"display": {"show_cost": True}})

    wrote = seed_neutral_personality_default(config_path)

    assert wrote is True
    config = load_yaml_config(config_path)
    assert config["display"]["personality"] == "default"
    assert config["display"]["show_cost"] is True


def test_personality_seed_respects_existing_value(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"display": {"personality": "kawaii"}})

    wrote = seed_neutral_personality_default(config_path)

    assert wrote is False
    config = load_yaml_config(config_path)
    assert config["display"]["personality"] == "kawaii"


def test_personality_seed_respects_existing_empty_string(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"display": {"personality": ""}})

    wrote = seed_neutral_personality_default(config_path)

    assert wrote is False
    config = load_yaml_config(config_path)
    assert config["display"]["personality"] == ""


# ---------------------------------------------------------------------------
# seed_show_cost_default
# ---------------------------------------------------------------------------


def test_show_cost_seed_writes_true_when_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    wrote = seed_show_cost_default(config_path)

    assert wrote is True
    config = load_yaml_config(config_path)
    assert config["display"]["show_cost"] is True


def test_show_cost_seed_respects_existing_false(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"display": {"show_cost": False}})

    wrote = seed_show_cost_default(config_path)

    assert wrote is False
    config = load_yaml_config(config_path)
    assert config["display"]["show_cost"] is False


# ---------------------------------------------------------------------------
# apply_first_boot_seeds
# ---------------------------------------------------------------------------


def test_apply_first_boot_seeds_returns_expected_dict_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    result = apply_first_boot_seeds(config_path)

    assert set(result.keys()) == {
        "memory_provider",
        "mcp_servers",
        "neutral_personality",
        "show_cost",
        "safer_agent_defaults",
        "browser_toolset",
    }
    assert all(isinstance(v, bool) for v in result.values())
    # On a fresh config, every seeder should have written.
    assert all(result.values())

    # Second invocation: idempotent — nothing should write.
    second = apply_first_boot_seeds(config_path)
    assert second == {
        "memory_provider": False,
        "mcp_servers": False,
        "neutral_personality": False,
        "show_cost": False,
        "safer_agent_defaults": False,
        "browser_toolset": False,
    }
