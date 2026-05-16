"""Upgrade visibility helpers for /admin/upgrade."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import is_authenticated, require_admin

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0
_CACHE_TTL = 1800  # 30 minutes

_GH_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
_GH_TAGS = "https://api.github.com/repos/{repo}/tags?per_page=1"

_COMPONENT_DEFS = [
    {
        "key": "hermes_station",
        "label": "hermes-station",
        "repo": "roryford/hermes-station",
        "use_tags": False,
        "release_url": "https://github.com/roryford/hermes-station/releases",
    },
    {
        "key": "hermes_agent",
        "label": "hermes-agent",
        "repo": "NousResearch/hermes-agent",
        "use_tags": True,
        "release_url": "https://github.com/NousResearch/hermes-agent/releases",
    },
    {
        "key": "hermes_webui",
        "label": "hermes-webui",
        "repo": "NousResearch/hermes-webui",
        "use_tags": False,
        "release_url": "https://github.com/NousResearch/hermes-webui/releases",
    },
]


def _normalise(ver: str | None) -> str:
    """Strip leading 'v' for comparison."""
    if not ver:
        return ""
    return ver.lstrip("v")


async def _fetch_latest(repo: str, use_tags: bool) -> str | None:
    """Return the latest version tag string for a repo, or None on error."""
    url = _GH_TAGS.format(repo=repo) if use_tags else _GH_LATEST.format(repo=repo)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            return None
        data = resp.json()
        if use_tags:
            return data[0]["name"] if data else None
        return data.get("tag_name")
    except Exception:  # noqa: BLE001
        return None


async def fetch_upgrade_info(current_versions: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch latest versions for all components. Returns a row per component."""
    rows = []
    for defn in _COMPONENT_DEFS:
        key = defn["key"]
        current = str(current_versions.get(key) or "unknown")
        latest = await _fetch_latest(defn["repo"], defn["use_tags"])

        if current == "unknown" or not latest:
            status = "unknown"
        elif _normalise(current) == _normalise(latest):
            status = "ok"
        else:
            status = "update_available"

        rows.append(
            {
                "key": key,
                "label": defn["label"],
                "current": current,
                "latest": latest or "unknown",
                "status": status,
                "release_url": defn["release_url"],
            }
        )
    return rows


def _get_cached(request: Request) -> tuple[list[dict[str, Any]] | None, float]:
    cache = getattr(request.app.state, "_upgrade_cache", None)
    if cache is None:
        return None, 0.0
    return cache.get("rows"), cache.get("ts", 0.0)


def _set_cache(request: Request, rows: list[dict[str, Any]]) -> None:
    request.app.state._upgrade_cache = {"rows": rows, "ts": time.monotonic()}


def _current_versions(request: Request) -> dict[str, Any]:
    readiness = getattr(request.app.state, "readiness", None)
    if readiness is not None and hasattr(readiness, "versions"):
        return dict(readiness.versions)
    return {}


async def upgrade_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    rows, ts = _get_cached(request)
    age = time.monotonic() - ts if ts else None
    return _templates.TemplateResponse(
        request,
        "admin/upgrade.html",
        {
            "active": "upgrade",
            "title": "Upgrade",
            "rows": rows or [],
            "cache_age_minutes": int(age // 60) if age is not None else None,
            "current_versions": _current_versions(request),
        },
    )


async def upgrade_check(request: Request) -> Response:
    """POST /admin/_partial/upgrade/check — fetch latest versions and return the table fragment."""
    if not is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows, ts = _get_cached(request)
    age = time.monotonic() - ts if ts else None
    if rows is not None and age is not None and age < _CACHE_TTL:
        # Serve from cache
        pass
    else:
        rows = await fetch_upgrade_info(_current_versions(request))
        _set_cache(request, rows)
        age = 0.0
    return _templates.TemplateResponse(
        request,
        "admin/_upgrade_table.html",
        {"rows": rows, "cache_age_minutes": int(age // 60) if age else 0},
    )


def routes() -> list[Route]:
    return [
        Route("/admin/upgrade", upgrade_page, methods=["GET"]),
        Route("/admin/_partial/upgrade/check", upgrade_check, methods=["POST"]),
    ]
