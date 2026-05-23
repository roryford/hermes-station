"""First-boot seeder for safer delegation + security defaults.

Mirrors upstream `cli-config.yaml.example` from hermes-agent. Per-key
no-clobber per CONTRACT.md §3.3 — user edits always win.
"""

from __future__ import annotations

from pathlib import Path

from hermes_station.config import (
    load_yaml_config,
    seed_safer_agent_defaults,
    write_yaml_config,
)


def test_fresh_config_gets_all_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    written = seed_safer_agent_defaults(config_path)

    assert set(written["delegation"]) == {
        "subagent_auto_approve",
        "max_concurrent_children",
        "max_spawn_depth",
    }
    assert written["security"] == ["tirith_enabled"]

    config = load_yaml_config(config_path)
    assert config["delegation"] == {
        "subagent_auto_approve": False,
        "max_concurrent_children": 3,
        "max_spawn_depth": 1,
    }
    assert config["security"] == {"tirith_enabled": True}


def test_user_subagent_auto_approve_true_not_overwritten(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"delegation": {"subagent_auto_approve": True}})

    written = seed_safer_agent_defaults(config_path)

    # The flag itself was preserved; sibling defaults still filled in.
    assert "subagent_auto_approve" not in written["delegation"]
    assert set(written["delegation"]) == {"max_concurrent_children", "max_spawn_depth"}

    config = load_yaml_config(config_path)
    assert config["delegation"]["subagent_auto_approve"] is True
    assert config["delegation"]["max_concurrent_children"] == 3
    assert config["delegation"]["max_spawn_depth"] == 1


def test_user_tirith_disabled_not_overwritten(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_yaml_config(config_path, {"security": {"tirith_enabled": False}})

    written = seed_safer_agent_defaults(config_path)

    assert written["security"] == []

    config = load_yaml_config(config_path)
    assert config["security"]["tirith_enabled"] is False


def test_second_invocation_is_noop(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    seed_safer_agent_defaults(config_path)

    written = seed_safer_agent_defaults(config_path)

    assert written == {"delegation": [], "security": []}
