"""Tests for config normalization + the broadened first-boot seeders.

`normalize_config` heals known shape-drift (string env_passthrough,
stray top-level env_passthrough). The new seeders mirror the no-clobber
contract documented in CONTRACT.md §3.3.
"""

from __future__ import annotations

from pathlib import Path

from hermes_station.config import (
    apply_first_boot_seeds,
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
    }
