"""Tests for hermes_station.secrets — mask and resolve helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_station.secrets import mask, resolve


# ─────────────────────────────────────────────────────────── mask


def test_mask_empty_string() -> None:
    assert mask("") == ""


def test_mask_short_value() -> None:
    assert mask("ab") == "***"
    assert mask("abcdef", head=4, tail=2) == "***"  # len 6 == head+tail


def test_mask_long_value() -> None:
    result = mask("sk-anthropic-xyz", head=4, tail=2)
    assert result.startswith("sk-a")
    assert result.endswith("yz")
    assert "…" in result


def test_mask_exact_boundary() -> None:
    # len=5, head=4, tail=2 → 5 <= 6 → "***"
    assert mask("abcde", head=4, tail=2) == "***"


# ─────────────────────────────────────────────────────────── resolve


def test_resolve_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "env-value")
    sv = resolve("MY_SECRET", {})
    assert sv.value == "env-value"
    assert sv.source == "env"


def test_resolve_from_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    sv = resolve("MY_SECRET", {"MY_SECRET": "file-value"})
    assert sv.value == "file-value"
    assert sv.source == "file"


def test_resolve_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    sv = resolve("MY_SECRET", {})
    assert sv.value is None
    assert sv.source == "unset"


def test_resolve_env_takes_precedence_over_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "env-wins")
    sv = resolve("MY_SECRET", {"MY_SECRET": "file-loses"})
    assert sv.value == "env-wins"
    assert sv.source == "env"


# ─────────────────────────────────────────────────────────── resolve_many


def test_resolve_many_returns_all_keys(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_station.secrets import resolve_many
    from hermes_station.config import Paths, write_env_file

    monkeypatch.delenv("MY_KEY_A", raising=False)
    monkeypatch.delenv("MY_KEY_B", raising=False)

    paths = Paths()
    write_env_file(paths.env_path, {"MY_KEY_A": "val-a"})

    result = resolve_many(["MY_KEY_A", "MY_KEY_B"], paths.env_path)
    assert result["MY_KEY_A"].value == "val-a"
    assert result["MY_KEY_A"].source == "file"
    assert result["MY_KEY_B"].source == "unset"
