"""Configuration: paths, settings, and the legacy file-format readers/writers.

This module is the single source of truth for the data contract documented in
`docs/CONTRACT.md`. The byte-level formats for `.env` and `config.yaml` are
chosen so that an existing /data volume from hermes-all-in-one mounts unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Paths(BaseSettings):
    """Filesystem paths under the /data volume.

    All defaults match the hermes-all-in-one contract. Each may be overridden
    by the same env var name that hermes-all-in-one honors.
    """

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    home: Path = Field(Path("/data"), alias="HOME")
    hermes_home: Path = Field(Path("/data/.hermes"), alias="HERMES_HOME")
    config_path: Path = Field(Path("/data/.hermes/config.yaml"), alias="HERMES_CONFIG_PATH")
    webui_state_dir: Path = Field(Path("/data/webui"), alias="HERMES_WEBUI_STATE_DIR")
    workspace_dir: Path = Field(Path("/data/workspace"), alias="HERMES_WORKSPACE_DIR")
    webui_src: Path = Field(Path("/opt/hermes-webui"), alias="HERMES_WEBUI_SRC")

    @property
    def env_path(self) -> Path:
        return self.hermes_home / ".env"

    @property
    def pairing_dir(self) -> Path:
        return self.hermes_home / "pairing"

    def ensure(self) -> None:
        """Create the directory skeleton documented in CONTRACT.md §3.1.

        Idempotent. Does not seed files — that's hermes-agent's job at first run.
        """
        for path in (
            self.home,
            self.hermes_home,
            self.webui_state_dir,
            self.workspace_dir,
            self.hermes_home / "sessions",
            self.hermes_home / "skills",
            self.hermes_home / "optional-skills",
            self.pairing_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


class AdminSettings(BaseSettings):
    """Admin-plane (control-plane) settings, all set via env vars."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    admin_password: str = Field("", alias="HERMES_ADMIN_PASSWORD")
    webui_password: str = Field("", alias="HERMES_WEBUI_PASSWORD")
    admin_session_ttl: int = Field(86400, alias="HERMES_ADMIN_SESSION_TTL")
    gateway_autostart: str = Field("auto", alias="HERMES_GATEWAY_AUTOSTART")

    @property
    def effective_admin_password(self) -> str:
        """Admin password falls back to the WebUI password if unset — same as hermes-all-in-one."""
        return self.admin_password or self.webui_password


def load_env_file(path: Path) -> dict[str, str]:
    """Read `$HERMES_HOME/.env` per CONTRACT.md §4.1.

    Skips blank lines and lines starting with `#`. Strips surrounding quotes
    from values. Same behavior as hermes-all-in-one so existing files load cleanly.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write `$HERMES_HOME/.env` per CONTRACT.md §4.1.

    Sorted alphabetically. Mode 0600. Atomic write via temp file + rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={values[key]}" for key in sorted(values)]
    body = "\n".join(lines) + ("\n" if lines else "")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load_yaml_config(path: Path) -> dict[str, Any]:
    """Read `$HERMES_HOME/config.yaml` per CONTRACT.md §4.2. Returns `{}` if missing."""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at top level, got {type(data).__name__}")
    return data


def write_yaml_config(path: Path, data: dict[str, Any]) -> None:
    """Write `$HERMES_HOME/config.yaml` per CONTRACT.md §4.2. Mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


class ModelConfig(BaseModel):
    """Shape of the `model:` block in config.yaml. See CONTRACT.md §4.2."""

    provider: str = ""
    default: str = ""
    base_url: str = ""


def extract_model_config(config: dict[str, Any]) -> ModelConfig:
    raw = config.get("model") or {}
    if not isinstance(raw, dict):
        return ModelConfig()
    return ModelConfig(
        provider=str(raw.get("provider") or ""),
        default=str(raw.get("default") or ""),
        base_url=str(raw.get("base_url") or ""),
    )
