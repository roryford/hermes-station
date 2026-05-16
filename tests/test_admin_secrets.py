"""Unit tests for hermes_station.admin.secrets_catalog.

Covers the data model (auto/override/disabled state resolution, shadow
detection), persistence helpers (save_override, clear_override, disable,
enable, add_custom_key, forget_custom_key), input validation, and
``secret_status`` rendering with both catalog and custom keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_station.admin import secrets_catalog as sc
from hermes_station.config import load_env_file, load_yaml_config


# ---------------------------------------------------------------------------
# is_valid_key_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["FOO", "FAL_KEY", "_HIDDEN", "A1", "MY_SERVICE_API_KEY", "_X9_Y"],
)
def test_is_valid_key_name_accepts_valid(name: str) -> None:
    assert sc.is_valid_key_name(name)


@pytest.mark.parametrize(
    "name",
    ["", "lower", "MixedCase", "1LEADING_DIGIT", "HAS SPACE", "HAS-DASH", "HAS$SIGN", "\n"],
)
def test_is_valid_key_name_rejects_invalid(name: str) -> None:
    assert not sc.is_valid_key_name(name)


def test_is_valid_key_name_rejects_overlong() -> None:
    assert not sc.is_valid_key_name("A" * 129)


# ---------------------------------------------------------------------------
# _resolve_state
# ---------------------------------------------------------------------------


def test_resolve_state_unset() -> None:
    state = sc._resolve_state("FAL_KEY", {}, {}, set())
    assert state["state"] == "auto"
    assert state["source"] == "unset"
    assert state["masked_value"] == ""
    assert state["shadowed"] is False


def test_resolve_state_env_only() -> None:
    state = sc._resolve_state("FAL_KEY", {}, {"FAL_KEY": "fal-xyz-1234"}, set())
    assert state["state"] == "auto"
    assert state["source"] == "env"
    assert "fal-" in state["masked_value"]
    assert state["shadowed"] is False


def test_resolve_state_file_only() -> None:
    state = sc._resolve_state("FAL_KEY", {"FAL_KEY": "override-1234"}, {}, set())
    assert state["state"] == "override"
    assert state["source"] == "file"
    assert "over" in state["masked_value"]
    assert state["shadowed"] is False
    assert state["railway_value"] == ""


def test_resolve_state_shadow_different_values() -> None:
    """Override differs from Railway value — surface shadow warning."""
    state = sc._resolve_state(
        "FAL_KEY",
        {"FAL_KEY": "my-override-abcd"},
        {"FAL_KEY": "railway-value-xyz"},
        set(),
    )
    assert state["state"] == "override"
    assert state["source"] == "file"
    assert state["shadowed"] is True
    assert state["railway_value"]


def test_resolve_state_shadow_same_value_no_warning() -> None:
    """Override equal to Railway is a redundant override, not a surprising shadow."""
    state = sc._resolve_state(
        "FAL_KEY",
        {"FAL_KEY": "same-1234"},
        {"FAL_KEY": "same-1234"},
        set(),
    )
    assert state["shadowed"] is False


def test_resolve_state_disabled_wins_over_env() -> None:
    state = sc._resolve_state(
        "FAL_KEY",
        {"FAL_KEY": "ignored-file"},
        {"FAL_KEY": "ignored-railway"},
        {"FAL_KEY"},
    )
    assert state["state"] == "disabled"
    assert state["source"] == "disabled"
    assert state["masked_value"] == ""


# ---------------------------------------------------------------------------
# get_custom_keys / get_disabled_keys parsing tolerance
# ---------------------------------------------------------------------------


def test_get_custom_keys_dedupes_and_validates() -> None:
    config = {
        "admin": {
            "custom_secret_keys": ["FOO", "FOO", "lower-bad", "BAR", ""],
        }
    }
    assert sc.get_custom_keys(config) == ["FOO", "BAR"]


def test_get_custom_keys_missing_admin_block() -> None:
    assert sc.get_custom_keys({}) == []


def test_get_custom_keys_wrong_shape() -> None:
    assert sc.get_custom_keys({"admin": {"custom_secret_keys": "not-a-list"}}) == []


def test_get_disabled_keys_returns_set() -> None:
    config = {"admin": {"disabled_secrets": ["FAL_KEY", " GH_TOKEN ", ""]}}
    assert sc.get_disabled_keys(config) == {"FAL_KEY", "GH_TOKEN"}


# ---------------------------------------------------------------------------
# save_override
# ---------------------------------------------------------------------------


def test_save_override_writes_env_and_registers_custom(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"

    sc.save_override(env_path, config_path, "MY_CUSTOM_KEY", "secret-value")

    values = load_env_file(env_path)
    assert values["MY_CUSTOM_KEY"] == "secret-value"
    # Custom keys get auto-tracked.
    config = load_yaml_config(config_path)
    assert "MY_CUSTOM_KEY" in sc.get_custom_keys(config)


def test_save_override_known_catalog_key_no_custom_register(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"

    sc.save_override(env_path, config_path, "FAL_KEY", "fal-test-1234")
    config = load_yaml_config(config_path)
    # FAL_KEY is in the static catalog, so it should NOT be added to custom list.
    assert "FAL_KEY" not in sc.get_custom_keys(config)


def test_save_override_clears_disabled_flag(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"

    sc.disable(config_path, "FAL_KEY")
    assert "FAL_KEY" in sc.get_disabled_keys(load_yaml_config(config_path))

    sc.save_override(env_path, config_path, "FAL_KEY", "new-value")
    # Setting an override implies "use this" — disabled flag must clear.
    assert "FAL_KEY" not in sc.get_disabled_keys(load_yaml_config(config_path))


def test_save_override_rejects_empty(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    with pytest.raises(ValueError, match="empty"):
        sc.save_override(env_path, config_path, "FAL_KEY", "   ")


def test_save_override_rejects_newline(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    with pytest.raises(ValueError, match="newline"):
        sc.save_override(env_path, config_path, "FAL_KEY", "value\nevil")


def test_save_override_rejects_invalid_key(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    with pytest.raises(ValueError, match="invalid key"):
        sc.save_override(env_path, config_path, "lower-case", "v")


# ---------------------------------------------------------------------------
# clear_override
# ---------------------------------------------------------------------------


def test_clear_override_removes_from_env(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    sc.save_override(env_path, config_path, "FAL_KEY", "v")
    sc.clear_override(env_path, "FAL_KEY")
    assert "FAL_KEY" not in load_env_file(env_path)


def test_clear_override_missing_key_noop(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    # Should not raise even when key isn't present.
    sc.clear_override(env_path, "NEVER_SET")


# ---------------------------------------------------------------------------
# disable / enable
# ---------------------------------------------------------------------------


def test_disable_adds_and_persists_sorted(fake_data_dir: Path) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    sc.disable(config_path, "ZULU_KEY")
    sc.disable(config_path, "ALPHA_KEY")
    raw = yaml.safe_load(config_path.read_text())["admin"]["disabled_secrets"]
    assert raw == ["ALPHA_KEY", "ZULU_KEY"]


def test_disable_idempotent(fake_data_dir: Path) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    sc.disable(config_path, "FAL_KEY")
    sc.disable(config_path, "FAL_KEY")
    assert sc.get_disabled_keys(load_yaml_config(config_path)) == {"FAL_KEY"}


def test_enable_removes(fake_data_dir: Path) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    sc.disable(config_path, "FAL_KEY")
    sc.enable(config_path, "FAL_KEY")
    assert sc.get_disabled_keys(load_yaml_config(config_path)) == set()


def test_enable_noop_when_absent(fake_data_dir: Path) -> None:
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    sc.enable(config_path, "WAS_NEVER_DISABLED")
    # No crash, no spurious admin block creation either.
    config = load_yaml_config(config_path)
    assert sc.get_disabled_keys(config) == set()


# ---------------------------------------------------------------------------
# add_custom_key / forget_custom_key
# ---------------------------------------------------------------------------


def test_add_custom_key_then_forget_round_trip(fake_data_dir: Path) -> None:
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"

    sc.add_custom_key(config_path, "MY_KEY")
    assert "MY_KEY" in sc.get_custom_keys(load_yaml_config(config_path))

    sc.save_override(env_path, config_path, "MY_KEY", "v")
    assert load_env_file(env_path)["MY_KEY"] == "v"

    sc.forget_custom_key(env_path, config_path, "MY_KEY")
    assert "MY_KEY" not in sc.get_custom_keys(load_yaml_config(config_path))
    assert "MY_KEY" not in load_env_file(env_path)


def test_add_custom_key_ignores_catalog_keys(fake_data_dir: Path) -> None:
    """Adding a catalog key as custom is a no-op — catalog rows render regardless."""
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    sc.add_custom_key(config_path, "FAL_KEY")
    assert "FAL_KEY" not in sc.get_custom_keys(load_yaml_config(config_path))


def test_forget_preserves_disabled_flag(fake_data_dir: Path) -> None:
    """Forgetting a custom key must not silently un-disable it.

    Recovery path: the orphaned disabled key shows up in secret_status under
    the ``extras`` branch so the operator can re-enable it.
    """
    env_path = fake_data_dir / ".hermes" / ".env"
    config_path = fake_data_dir / ".hermes" / "config.yaml"
    sc.add_custom_key(config_path, "MY_KEY")
    sc.disable(config_path, "MY_KEY")
    sc.forget_custom_key(env_path, config_path, "MY_KEY")
    assert "MY_KEY" in sc.get_disabled_keys(load_yaml_config(config_path))


# ---------------------------------------------------------------------------
# secret_status — rendering
# ---------------------------------------------------------------------------


def test_secret_status_includes_all_catalog_keys(fake_data_dir: Path) -> None:
    ctx = sc.secret_status({}, {}, environ={})
    keys = {row["key"] for row in ctx["rows"]}
    for entry in sc.KNOWN_SECRETS:
        assert entry["key"] in keys


def test_secret_status_includes_custom_keys(fake_data_dir: Path) -> None:
    ctx = sc.secret_status(
        {"admin": {"custom_secret_keys": ["MY_KEY"]}},
        {"MY_KEY": "v"},
        environ={},
    )
    custom_rows = [r for r in ctx["rows"] if r["is_custom"]]
    assert any(r["key"] == "MY_KEY" for r in custom_rows)


def test_secret_status_orphan_disabled_surfaces(fake_data_dir: Path) -> None:
    """A disabled key not in catalog or custom list still gets a row.

    This is the recovery affordance after ``forget_custom_key``.
    """
    ctx = sc.secret_status(
        {"admin": {"disabled_secrets": ["ORPHAN_KEY"]}},
        {},
        environ={},
    )
    assert any(r["key"] == "ORPHAN_KEY" and r["source"] == "disabled" for r in ctx["rows"])


def test_secret_status_groups_in_catalog_order(fake_data_dir: Path) -> None:
    ctx = sc.secret_status({}, {}, environ={})
    group_slugs = [g["slug"] for g in ctx["groups"]]
    # Catalog groups appear in declared order; "custom" only appears if used.
    for slug in sc.CATALOG_GROUPS:
        if slug in group_slugs:
            assert group_slugs.index(slug) >= 0
    # Strictly increasing position for declared groups.
    declared = [s for s in sc.CATALOG_GROUPS if s in group_slugs]
    assert declared == [s for s in group_slugs if s != "custom"]


def test_secret_status_marks_in_process(fake_data_dir: Path) -> None:
    ctx = sc.secret_status({}, {}, environ={"FAL_KEY": "v"})
    fal_row = next(r for r in ctx["rows"] if r["key"] == "FAL_KEY")
    assert fal_row["in_process"] is True
    assert fal_row["source"] == "env"
