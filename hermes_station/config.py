"""Configuration: paths, settings, and the legacy file-format readers/writers.

This module is the single source of truth for the data contract documented in
`docs/CONTRACT.md`. The byte-level formats for `.env` and `config.yaml` are
chosen to keep existing /data volumes mountable without migration.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("hermes_station.config")


class Paths(BaseSettings):
    """Filesystem paths under the /data volume. Defaults follow the Hermes data contract."""

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
        """Admin password falls back to the WebUI password if unset."""
        return self.admin_password or self.webui_password


def load_env_file(path: Path) -> dict[str, str]:
    """Read `$HERMES_HOME/.env` per CONTRACT.md §4.1.

    Skips blank lines and lines starting with `#`. Strips surrounding quotes
    from values. Existing `.env` files load cleanly.
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
    _seed_gh_cli_hosts(os.environ.get("GH_TOKEN") or github_token)


def _seed_gh_cli_hosts(token: str) -> None:
    """Write ~/.config/gh/hosts.yml so gh CLI authenticates in all tool sandboxes.

    hermes-agent's env blocklist strips GH_TOKEN and GITHUB_TOKEN from sandboxed
    subprocess environments (execute_code, terminal_tool) because they are Copilot
    accepted credentials. Stored credentials in hosts.yml bypass this entirely —
    gh reads the file before checking env vars, making it visible in every context.

    Written on every call so token rotation (Railway PAT refresh, OAuth re-auth)
    is picked up without a manual step. Mode 0600 via _write_secret_file.
    """
    if not token:
        return
    home = Path(os.environ.get("HOME", "/data"))
    gh_dir = home / ".config" / "gh"
    gh_dir.mkdir(parents=True, exist_ok=True)
    hosts_path = gh_dir / "hosts.yml"
    body = f"github.com:\n    oauth_token: {token}\n    git_protocol: https\n"
    _write_secret_file(hosts_path, body)


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


def seed_default_memory_provider(path: Path, *, provider: str = DEFAULT_MEMORY_PROVIDER) -> bool:
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


# Provider seeder ----------------------------------------------------------
#
# Default model picks per provider. Update when models retire — see
# hermes-agent provider docs. Tier policy: pick the current "balanced
# flagship" for each provider (DX-reviewer flagged tier asymmetry against
# `gpt-4o-mini` in earlier drafts).
DEFAULT_MODELS_BY_PROVIDER: dict[str, str] = {
    "openrouter": "anthropic/claude-sonnet-4.5",
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4.1",
}


# Order = precedence on multi-key set. OpenRouter first because it's the
# template's headline path.
PROVIDER_ENV_KEYS: list[tuple[str, str]] = [
    ("openrouter", "OPENROUTER_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
]


def seed_provider_from_env(path: Path, env: Mapping[str, str]) -> str | None:
    """First-boot seed: pick a model provider from env vars and write to config.yaml.

    Walks ``PROVIDER_ENV_KEYS`` in order; the first env var with a non-empty,
    non-whitespace value wins. Writes ``model: {provider, default}`` to
    ``config.yaml`` using the corresponding default from
    ``DEFAULT_MODELS_BY_PROVIDER``. The field is named ``default`` (not
    ``name``) because that's what ``extract_model_config`` reads — writing
    ``name`` made the field a no-op and triggered a runtime warning from
    the readiness probe.

    No-clobber per CONTRACT.md §3.3 is **absolute**: if any ``model:`` block
    already exists in config.yaml (even a partial one like
    ``model: {name: foo}`` with no ``provider``), this function does nothing.
    Operators who edited the file get to keep their state.

    Always emits one INFO log line describing the outcome — never silently
    skips, so deploy-time `railway logs` makes the chosen path obvious.

    Returns the seeded provider name, or ``None`` if nothing was written.
    """
    config = load_yaml_config(path)
    model_block = config.get("model")
    # Treat any pre-existing model block as user-configured (even with no
    # `provider` key) — see test_seed_provider_from_env_partial_model_block_no_clobber.
    if isinstance(model_block, dict) and model_block:
        existing = str(model_block.get("provider") or "").strip()
        if existing:
            logger.info("seed_provider_from_env: skipped: model.provider already set to %s", existing)
        else:
            logger.info(
                "seed_provider_from_env: skipped: model block already present (no provider key); leaving untouched"
            )
        return None

    # Find the first usable key. Track empty-but-set keys so we can log them.
    chosen: tuple[str, str] | None = None
    empty_keys: list[str] = []
    other_present: list[str] = []
    for provider_name, env_key in PROVIDER_ENV_KEYS:
        if env_key not in env:
            continue
        value = (env.get(env_key) or "").strip()
        if not value:
            empty_keys.append(env_key)
            continue
        if chosen is None:
            chosen = (provider_name, env_key)
        else:
            other_present.append(env_key)

    if chosen is None:
        if empty_keys:
            logger.info(
                "seed_provider_from_env: skipped: %s present but empty/whitespace",
                ", ".join(empty_keys),
            )
        else:
            logger.info("seed_provider_from_env: skipped: no recognized provider key in env")
        return None

    provider_name, env_key = chosen
    default_model = DEFAULT_MODELS_BY_PROVIDER[provider_name]
    config["model"] = {"provider": provider_name, "default": default_model}
    write_yaml_config(path, config)
    logger.info(
        "seed_provider_from_env: seeded provider=%s model=%s from %s (other provider keys present: %s)",
        provider_name,
        default_model,
        env_key,
        other_present or [],
    )
    return provider_name


def detect_provider_drift(config: dict[str, Any], env: Mapping[str, str]) -> list[str]:
    """Detect mismatches between configured provider and present env credentials.

    Emits a single human-readable warning string when ``model.provider`` is
    set but its expected env var is missing **and** a different provider's
    key is present — the actionable case where switching providers would
    take seconds via ``/admin/settings``.

    No auto-reseed: no-clobber stays absolute. Returns an empty list when
    nothing is actionable (e.g. no other provider keys are set, or the
    configured provider's env var is present).
    """
    model_block = config.get("model")
    if not isinstance(model_block, dict):
        return []
    provider = str(model_block.get("provider") or "").strip().lower()
    if not provider:
        return []

    # Find the env key for the configured provider, if recognized.
    expected_key: str | None = None
    for name, key in PROVIDER_ENV_KEYS:
        if name == provider:
            expected_key = key
            break
    if expected_key is None:
        return []

    if (env.get(expected_key) or "").strip():
        return []

    # The configured provider has no usable credential. Look for an alternative.
    alternates: list[str] = []
    for name, key in PROVIDER_ENV_KEYS:
        if name == provider:
            continue
        if (env.get(key) or "").strip():
            alternates.append(key)
    if not alternates:
        return []

    msg = (
        f"provider {provider!r} is configured but {expected_key} is unset; "
        f"{', '.join(alternates)} is set — open /admin/settings to switch"
    )
    return [msg]


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


def seed_default_mcp_servers(path: Path, *, catalog: list[dict[str, Any]] | None = None) -> list[str]:
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


def normalize_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Heal common config-shape drift in-place. Returns (config, changes).

    Two known sources of breakage on real /data volumes:

    1. `terminal.env_passthrough` written as a comma-separated string (YAML
       scalar that should have been a list) — hermes-agent reads it as a
       string and silently passes nothing through.
    2. A stray top-level `env_passthrough:` key with a blank/null value,
       the broken sibling of `terminal.env_passthrough` that older versions
       used to write. It does nothing but clutter the file.

    Idempotent: a second call on the result yields an empty changes list.
    Does NOT seed defaults — see the `seed_*` functions for that.
    """
    changes: list[str] = []

    terminal = config.get("terminal")
    if isinstance(terminal, dict) and "env_passthrough" in terminal:
        ep = terminal["env_passthrough"]
        if isinstance(ep, str):
            items = [item.strip() for item in ep.split(",") if item.strip()]
            terminal["env_passthrough"] = items
            changes.append(f"coerced terminal.env_passthrough from string to list ({len(items)} entries)")

    if "env_passthrough" in config:
        value = config["env_passthrough"]
        is_blank = value is None or (isinstance(value, (str, list, dict)) and len(value) == 0)
        if is_blank:
            del config["env_passthrough"]
            changes.append("removed blank top-level env_passthrough key")

    return config, changes


def seed_neutral_personality_default(path: Path) -> bool:
    """First-boot seed: set `display.personality: "default"` if unset.

    Mirrors `seed_default_memory_provider`. A deliberate neutral value chosen
    over the historical accidental `kawaii` default — operators expect a
    polished, generic persona out of the box.

    No-clobber per CONTRACT.md §3.3: any existing value (including "") wins.
    Returns True iff a write happened.
    """
    config = load_yaml_config(path)
    display = config.get("display")
    if isinstance(display, dict) and "personality" in display:
        return False
    if not isinstance(display, dict):
        display = {}
    display["personality"] = "default"
    config["display"] = display
    write_yaml_config(path, config)
    return True


def seed_show_cost_default(path: Path) -> bool:
    """First-boot seed: set `display.show_cost: True` if unset.

    Operator-facing default — surfaces token/dollar spend in the UI by default
    so accidental runaway loops are visible. No-clobber per CONTRACT.md §3.3.
    Returns True iff a write happened.
    """
    config = load_yaml_config(path)
    display = config.get("display")
    if isinstance(display, dict) and "show_cost" in display:
        return False
    if not isinstance(display, dict):
        display = {}
    display["show_cost"] = True
    config["display"] = display
    write_yaml_config(path, config)
    return True


def apply_first_boot_seeds(path: Path) -> dict[str, bool]:
    """Apply all first-boot seeders in sequence, returning a per-seed write map.

    Pure additive helper for future consolidation — `app.py` still calls the
    individual seeders. Each entry is True iff that seeder wrote to disk.
    `seed_default_mcp_servers` returns a list of names; we summarize as True
    iff any names were added.
    """
    results: dict[str, bool] = {}
    results["memory_provider"] = seed_default_memory_provider(path)
    results["mcp_servers"] = bool(seed_default_mcp_servers(path))
    results["neutral_personality"] = seed_neutral_personality_default(path)
    results["show_cost"] = seed_show_cost_default(path)
    return results


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
