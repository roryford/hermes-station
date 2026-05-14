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


def seed_env_file_to_os(path: Path) -> None:
    """Merge .env values into os.environ (CONTRACT.md §2.1 — .env takes precedence).

    Called before the in-process gateway task starts so credentials stored via
    the admin UI override any conflicting Railway / host environment variables.
    GITHUB_TOKEN is intentionally left in os.environ for gh CLI use; Copilot
    credential-pool pollution is handled via auth.json suppression instead
    (see _suppress_copilot_fallback_sources in admin/provider.py).

    Also writes _HERMES_FORCE_GITHUB_TOKEN and _HERMES_FORCE_GH_TOKEN so both
    vars reach terminal subprocesses despite being in hermes-agent's provider
    env blocklist (Copilot accepted credentials, stripped by default). GH_TOKEN
    is the preferred var for the gh CLI and for agent GitHub diagnostics; both
    are sourced from GITHUB_TOKEN (Railway injects it; GH_TOKEN is not set).
    The _HERMES_FORCE_ prefix is hermes-agent's escape hatch for exactly this.
    """
    env_file = load_env_file(path)
    os.environ.update(env_file)
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token:
        os.environ["_HERMES_FORCE_GITHUB_TOKEN"] = github_token
        os.environ.setdefault("GH_TOKEN", github_token)
        os.environ["_HERMES_FORCE_GH_TOKEN"] = os.environ["GH_TOKEN"]
    else:
        os.environ.pop("_HERMES_FORCE_GITHUB_TOKEN", None)
        os.environ.pop("_HERMES_FORCE_GH_TOKEN", None)


def _write_secret_file(path: Path, body: str) -> None:
    """Write *body* to *path* atomically with mode 0o600 from creation time.

    Uses os.open() so the file is never world-readable even for a moment —
    avoids the TOCTOU window of write_text() + os.chmod().
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    tmp.replace(path)


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write `$HERMES_HOME/.env` per CONTRACT.md §4.1.

    Sorted alphabetically. Mode 0600. Atomic write via temp file + rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={values[key]}" for key in sorted(values)]
    body = "\n".join(lines) + ("\n" if lines else "")
    _write_secret_file(path, body)


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
    _write_secret_file(path, body)


DEFAULT_MEMORY_PROVIDER = "holographic"


def seed_default_memory_provider(
    path: Path, *, provider: str = DEFAULT_MEMORY_PROVIDER
) -> bool:
    """First-boot seed: set `memory.provider` in config.yaml if unset.

    hermes-agent ships with 8 memory providers but none active by default —
    out of the box `hermes memory setup` is the only way to pick one. We
    default-enable the in-process holographic provider so fresh deployments
    have semantic memory immediately, with zero external dependencies.

    No-clobber per CONTRACT.md §3.3: if `memory.provider` is already set
    (even to ""), the existing value wins. Returns True iff a write happened.
    The seeded value is just the activation key — hermes-agent's plugin
    loader supplies sensible defaults for everything under
    `plugins.hermes-memory-store.*` if the section is absent.
    """
    config = load_yaml_config(path)
    memory = config.get("memory")
    # Already configured (any value, including "") — respect user choice.
    if isinstance(memory, dict) and "provider" in memory:
        return False
    if not isinstance(memory, dict):
        memory = {}
    memory["provider"] = provider
    config["memory"] = memory
    write_yaml_config(path, config)
    return True


MCP_SERVER_CATALOG: list[dict[str, Any]] = [
    {
        "name": "filesystem",
        "label": "Filesystem",
        "description": (
            "Read/write files inside the workspace dir. Scoped to "
            "$HERMES_WORKSPACE_DIR (`/data/workspace`) — the model cannot "
            "escape it."
        ),
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-filesystem@2025.8.21",
            "/data/workspace",
        ],
        "env": {},
        "needs": [],
    },
    {
        "name": "fetch",
        "label": "Web fetch",
        "description": (
            "Generic HTTP(S) fetcher with HTML→Markdown conversion. Useful "
            "for ad-hoc page reads when no purpose-built tool fits."
        ),
        "command": "uvx",
        "args": ["--from", "mcp-server-fetch==2025.4.7", "mcp-server-fetch"],
        "env": {},
        "needs": [],
    },
    {
        "name": "github",
        "label": "GitHub",
        "description": (
            "Issues, PRs, search, file reads via the GitHub REST API. Reuses "
            "GITHUB_TOKEN from your `gh auth login` / Railway env — no extra "
            "credential entry."
        ),
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"},
        "needs": ["GITHUB_TOKEN"],
    },
]


def _server_seed_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Build the on-disk config dict for one catalog entry.

    Default-off (`enabled: false`) per the lite-tier policy — users opt in
    explicitly from /admin. Stdio transport is implied by `command`/`args`.
    """
    seed: dict[str, Any] = {
        "command": entry["command"],
        "args": list(entry["args"]),
        "enabled": False,
    }
    if entry.get("env"):
        seed["env"] = dict(entry["env"])
    return seed


def seed_default_mcp_servers(
    path: Path, *, catalog: list[dict[str, Any]] | None = None
) -> list[str]:
    """First-boot seed: add curated stdio MCP servers to config.yaml, default-off.

    Each server in `MCP_SERVER_CATALOG` is added under the top-level
    `mcp_servers` key (the schema hermes-agent's `tools/mcp_tool.py` reads).
    Per-server no-clobber per CONTRACT.md §3.3: any server name already
    present is left untouched (preserving user `enabled: true` or any custom
    args/env). Returns the list of server names that were newly written.
    """
    catalog = catalog if catalog is not None else MCP_SERVER_CATALOG
    config = load_yaml_config(path)
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}
    added: list[str] = []
    for entry in catalog:
        name = entry["name"]
        if name in servers:
            continue
        servers[name] = _server_seed_entry(entry)
        added.append(name)
    if not added:
        return []
    config["mcp_servers"] = servers
    write_yaml_config(path, config)
    return added


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


def load_or_create_signing_key(paths: "Paths") -> bytes:
    """Load the session signing key from disk, or generate + persist a new one.

    The key is stored at $HERMES_HOME/.signing_key as a hex string (mode 0600).
    Hex encoding avoids the TOCTOU-adjacent bug where raw binary bytes that
    happen to be whitespace get stripped on read, producing a different key than
    was signed with. Generating a random key decouples session security from
    admin password strength and keeps sessions valid across password changes.
    """
    import secrets as _secrets
    key_path = paths.hermes_home / ".signing_key"
    if key_path.exists():
        hex_key = key_path.read_text(encoding="ascii").strip()
        if len(hex_key) >= 64:
            return bytes.fromhex(hex_key)
    # Generate a 64-byte (512-bit) random key stored as lowercase hex.
    key = _secrets.token_bytes(64)
    hex_key = key.hex()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    _write_secret_file(key_path, hex_key)
    return key
