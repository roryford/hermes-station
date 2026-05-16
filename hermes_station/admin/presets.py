"""Opinionated preset configurations for common Hermes deployment use cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import require_admin
from hermes_station.config import Paths, load_yaml_config, write_yaml_config


PRESET_CATALOG: list[dict[str, Any]] = [
    {
        "id": "chat_only",
        "label": "Chat-only WebUI",
        "description": "WebUI access only. No messaging channels, no gateway autostart. Good for local or private use.",
        "tags": ["WebUI"],
        "mcp_enable": [],
        "todo": [
            "Set HERMES_WEBUI_PASSWORD and HERMES_ADMIN_PASSWORD (if not already set)",
            "Add a provider key in Settings",
        ],
    },
    {
        "id": "telegram_starter",
        "label": "Telegram bot",
        "description": "Hermes as a Telegram bot. The gateway connects automatically once your provider and bot token are set.",
        "tags": ["Telegram", "Gateway"],
        "mcp_enable": [],
        "todo": [
            "Add a provider key in Settings",
            "Set TELEGRAM_BOT_TOKEN in Settings → Channels",
        ],
    },
    {
        "id": "research_assistant",
        "label": "Research assistant",
        "description": "WebUI with web search, HTTP fetch, and workspace filesystem tools enabled.",
        "tags": ["WebUI", "Web search", "Fetch MCP", "Filesystem MCP"],
        "mcp_enable": ["filesystem", "fetch"],
        "todo": [
            "Add a provider key in Settings",
            "Set BRAVE_API_KEY (or another search key) in Settings → Secrets to enable web search",
        ],
    },
    {
        "id": "github_helper",
        "label": "GitHub helper",
        "description": "Code assistant with GitHub MCP and workspace filesystem for issues, PRs, and file reads.",
        "tags": ["GitHub MCP", "Filesystem MCP"],
        "mcp_enable": ["github", "filesystem"],
        "todo": [
            "Add a provider key in Settings",
            "Set GITHUB_TOKEN in Settings → Secrets",
        ],
    },
]


def apply_preset(config_path: Path, preset_id: str) -> dict[str, Any]:
    """Apply a preset: enable the specified MCP servers. Returns the preset dict.

    Per-server no-clobber: if a server is already enabled, leave it. Only
    adds entries that are missing or flips disabled ones to enabled.
    Raises ValueError for unknown preset IDs.
    """
    preset = next((p for p in PRESET_CATALOG if p["id"] == preset_id), None)
    if preset is None:
        raise ValueError(f"unknown preset: {preset_id!r}")

    if not preset["mcp_enable"]:
        return preset  # nothing to write

    config = load_yaml_config(config_path)
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}

    changed = False
    for name in preset["mcp_enable"]:
        entry = servers.get(name)
        if isinstance(entry, dict):
            if not entry.get("enabled"):
                entry["enabled"] = True
                changed = True
        else:
            # Server not yet seeded — add a minimal enabled entry.
            servers[name] = {"enabled": True}
            changed = True

    if changed:
        config["mcp_servers"] = servers
        write_yaml_config(config_path, config)

    return preset


def _paths(request: Request) -> Paths:
    return request.app.state.paths


async def presets_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    return _templates.TemplateResponse(
        request,
        "admin/presets.html",
        {"active": "presets", "title": "Presets", "presets": PRESET_CATALOG},
    )


async def preset_apply(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths = _paths(request)
    preset_id = request.path_params["preset_id"]
    alert: dict[str, str] | None = None
    preset: dict[str, Any] | None = None
    try:
        preset = apply_preset(paths.config_path, preset_id)
        # Restart gateway so MCP changes take effect.
        gateway = getattr(request.app.state, "gateway", None)
        if gateway is not None:
            await gateway.restart()
        alert = {"kind": "success", "message": "Preset applied."}
    except ValueError as exc:
        alert = {"kind": "error", "message": str(exc)}
    except Exception:  # noqa: BLE001
        alert = {"kind": "error", "message": "Apply failed — check logs for details."}

    if preset is None:
        preset = next((p for p in PRESET_CATALOG if p["id"] == preset_id), None)

    return _templates.TemplateResponse(
        request,
        "admin/_preset_applied.html",
        {"preset": preset, "alert": alert},
    )


def routes() -> list[Route]:
    return [
        Route("/admin/presets", presets_page, methods=["GET"]),
        Route("/admin/_partial/presets/{preset_id}/apply", preset_apply, methods=["POST"]),
    ]
