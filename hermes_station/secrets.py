"""Layered secret resolution: os.environ wins over $HERMES_HOME/.env.

This is Option C from the rebuild plan. It lets users move secrets to Railway
env vars at their own pace: secrets in Railway take precedence; the legacy .env
file is still honored as a fallback so existing /data volumes keep working.

The admin UI uses `resolve()` to display the source of each secret with a badge.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hermes_station.config import load_env_file

Source = Literal["env", "file", "unset"]


@dataclass(frozen=True)
class SecretValue:
    value: str | None
    source: Source


def resolve(key: str, file_values: dict[str, str]) -> SecretValue:
    """Resolve a single secret. Pre-loaded file_values avoids re-reading .env per key."""
    if os.environ.get(key):
        return SecretValue(os.environ[key], "env")
    if file_values.get(key):
        return SecretValue(file_values[key], "file")
    return SecretValue(None, "unset")


def resolve_many(keys: list[str], env_path: Path) -> dict[str, SecretValue]:
    file_values = load_env_file(env_path)
    return {key: resolve(key, file_values) for key in keys}


def mask(value: str, head: int = 4, tail: int = 2) -> str:
    """Mask a secret for display. `sk-anthropic-xyz` → `sk-a…yz`."""
    if not value:
        return ""
    if len(value) <= head + tail:
        return "***"
    return f"{value[:head]}…{value[-tail:]}"
