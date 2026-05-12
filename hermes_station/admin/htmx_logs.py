"""HTMX-driven admin Logs viewer.

Renders ``/admin/logs`` with one pane per ring buffer (station/gateway/webui)
and exposes per-source fragment + JSON endpoints. The page polls each fragment
every 3 seconds via HTMX.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import is_authenticated, require_admin
from hermes_station.logs import BUFFERS

_SOURCES = tuple(BUFFERS.keys())
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 500


def _parse_limit(raw: str | None) -> int:
    if not raw:
        return _DEFAULT_LIMIT
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_LIMIT
    if n < 1:
        return 1
    return min(n, _MAX_LIMIT)


async def logs_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    return _templates.TemplateResponse(
        request,
        "admin/logs.html",
        {
            "active": "logs",
            "title": "Logs",
            "sources": _SOURCES,
        },
    )


async def logs_fragment(request: Request) -> Response:
    if not is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    source = request.path_params["source"]
    if source not in BUFFERS:
        return JSONResponse({"error": "unknown source"}, status_code=400)
    limit = _parse_limit(request.query_params.get("limit"))
    lines = BUFFERS[source].tail(limit)
    return _templates.TemplateResponse(
        request,
        "admin/_logs_pane.html",
        {"source": source, "lines": lines},
    )


async def api_logs(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    source = request.path_params["source"]
    if source not in BUFFERS:
        return JSONResponse({"error": "unknown source"}, status_code=400)
    limit = _parse_limit(request.query_params.get("limit"))
    lines = BUFFERS[source].tail(limit)
    return JSONResponse({"source": source, "lines": lines, "count": len(lines)})


def routes() -> list[Route]:
    return [
        Route("/admin/logs", logs_page, methods=["GET"]),
        Route("/admin/_partial/logs/{source}", logs_fragment, methods=["GET"]),
        Route("/admin/api/logs/{source}", api_logs, methods=["GET"]),
    ]
