"""ASGI application factory for hermes-station.

Phase 0: control plane only — /health, /admin login + stub status API.
Phase 1: mount hermes-webui as a sub-app, start the gateway as an asyncio task.

The single-process design means there is one event loop, one log stream, and
one shutdown signal. The lifespan handler owns startup/shutdown of any
background workloads.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from hermes_station.admin.routes import admin_routes
from hermes_station.config import Paths


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    paths: Paths = app.state.paths
    paths.ensure()
    # Phase 1 will:
    # - mount hermes-webui as a sub-app on "/"
    # - start the gateway as an asyncio task (supervised with restart-on-crash)
    yield


async def health(request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def webui_stub(request) -> PlainTextResponse:
    return PlainTextResponse(
        "hermes-station Phase 0 — WebUI mount lands in Phase 1. See /admin and /health.",
        status_code=503,
    )


def create_app() -> Starlette:
    paths = Paths()
    base_dir = Path(__file__).resolve().parent

    routes = [
        Route("/health", health),
        *admin_routes(),
        Mount("/admin/static", app=StaticFiles(directory=str(base_dir / "static"), check_dir=False), name="admin-static"),
        Route("/", webui_stub),
        Route("/{path:path}", webui_stub),
    ]

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.paths = paths
    return app


app = create_app()
