"""Architecture overview page at /admin/topology."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import require_admin
from hermes_station.config import Paths


async def topology_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    paths: Paths = request.app.state.paths
    return _templates.TemplateResponse(
        request,
        "admin/topology.html",
        {
            "active": "topology",
            "title": "Topology",
            "paths": paths,
        },
    )


def routes() -> list[Route]:
    return [
        Route("/admin/topology", topology_page, methods=["GET"]),
    ]
