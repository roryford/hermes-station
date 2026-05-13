"""MCP server admin helpers — status + per-server enable/disable toggle.

The on-disk schema is owned by hermes-agent (`tools/mcp_tool.py`):

    mcp_servers:
      <name>:
        command: "npx"
        args: [...]
        env: {KEY: "value"}
        enabled: true   # default true; `false` skips the server entirely

We seed `MCP_SERVER_CATALOG` (see `hermes_station.config`) on first boot
with `enabled: false`, then surface a card on /admin to toggle them. Toggle
writes back to config.yaml and triggers a gateway restart so the change
takes effect (MCP servers are loaded at gateway start).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_station.config import (
    MCP_SERVER_CATALOG,
    load_env_file,
    load_yaml_config,
    write_yaml_config,
)


_TRUTHY = {"1", "true", "yes", "on"}


def _is_enabled(raw: Any) -> bool:
    """Mirror hermes-agent's `_parse_boolish` semantics: default True."""
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return True
    if isinstance(raw, str):
        return raw.strip().lower() in _TRUTHY
    return bool(raw)


def mcp_status(
    config: dict[str, Any] | None,
    env_values: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Per-server state for the admin UI, in catalog order.

    `env_values` is the loaded `.env` dict — used to compute `needs_satisfied`
    for entries (like `github`) that depend on an env var being present.
    """
    servers = (config or {}).get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}
    env_values = env_values or {}
    out: list[dict[str, Any]] = []
    for entry in MCP_SERVER_CATALOG:
        name = entry["name"]
        cfg = servers.get(name) if isinstance(servers.get(name), dict) else {}
        enabled = _is_enabled(cfg.get("enabled")) if cfg else False
        configured = name in servers
        needs = entry.get("needs", []) or []
        # `${X}` interpolation happens at MCP launch time — for the UI we
        # just check whether each required key is non-empty in .env or os.env.
        import os
        needs_satisfied = all(
            (env_values.get(key) or os.environ.get(key) or "").strip()
            for key in needs
        )
        out.append(
            {
                "name": name,
                "label": entry["label"],
                "description": entry["description"],
                "command": entry["command"],
                "args": list(entry["args"]),
                "enabled": enabled,
                "configured": configured,
                "needs": list(needs),
                "needs_satisfied": needs_satisfied,
            }
        )
    return out


def toggle_mcp_server(config_path: Path, name: str) -> bool:
    """Flip the `enabled` flag for one MCP server. Returns the new value.

    Raises `ValueError` if the server name isn't in the on-disk config (the
    UI only exposes catalog entries, but the seed step should have written
    them — this guards against a hand-edited `mcp_servers` block that
    deleted an entry).
    """
    config = load_yaml_config(config_path)
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict) or name not in servers:
        raise ValueError(f"unknown MCP server: {name}")
    entry = servers[name]
    if not isinstance(entry, dict):
        raise ValueError(f"MCP server {name!r} has malformed config")
    new_value = not _is_enabled(entry.get("enabled"))
    entry["enabled"] = new_value
    config["mcp_servers"] = servers
    write_yaml_config(config_path, config)
    return new_value


def load_mcp_status(config_path: Path, env_path: Path) -> list[dict[str, Any]]:
    """Convenience: load config + env from disk and return status rows."""
    return mcp_status(load_yaml_config(config_path), load_env_file(env_path))
