"""Structured component-aware health surface for hermes-station.

Three endpoints:

* ``GET /health/live`` — cheap liveness; 200 if the process is up.
* ``GET /health/ready`` — composite readiness; 200 only when fully ok,
  503 when degraded or down. JSON body matches ``/health``.
* ``GET /health`` — full status payload, always 200. The body's ``status``
  field is the verdict (``ok|degraded|down``), so monitors can scrape it
  without alerting on degraded.

This module is the *operational source of truth* — explicitly NOT relying
on ``hermes gateway status``. The control plane composes the response from
the live `Gateway.snapshot()` / `WebUIProcess.snapshot()` plus the cached
boot-time readiness report on ``app.state.readiness``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

logger = logging.getLogger("hermes_station.health")


def _storage_block(paths: Any) -> dict[str, Any]:
    data_path = getattr(paths, "home", Path("/data"))
    config_path = getattr(paths, "config_path", Path("/data/.hermes/config.yaml"))

    data_writable = False
    try:
        Path(data_path).mkdir(parents=True, exist_ok=True)
        probe = Path(data_path) / ".health_probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink(missing_ok=True)
        data_writable = True
    except OSError:
        data_writable = False

    config_readable = True
    try:
        cp = Path(config_path)
        if cp.exists():
            # Read access only — small read for liveness, not full parse.
            with cp.open("rb") as fh:
                fh.read(1)
    except OSError:
        config_readable = False

    return {
        "data_writable": data_writable,
        "config_readable": config_readable,
    }


def _memory_block(readiness: Any, paths: Any) -> dict[str, Any]:
    """Memory subsystem state. Provider comes from config (via readiness summary);
    db_ok is approximated by the holographic memory readiness row.
    """
    provider = "none"
    db_ok = True
    if readiness is not None:
        rd = readiness.readiness if hasattr(readiness, "readiness") else {}
        # Look for memory:* in the readiness rows.
        for key, row in rd.items():
            if key.startswith("memory:"):
                provider = key.split(":", 1)[1]
                db_ok = bool(getattr(row, "ready", row.get("ready") if isinstance(row, dict) else False))
                break
        else:
            # Not in rows means not intended; fall back to summary if available.
            provider = "builtin"
            db_ok = True
    return {"provider": provider, "db_ok": db_ok}


def _scheduler_block(paths: Any) -> dict[str, Any]:
    """Best-effort: scheduler is hermes-agent's responsibility. We surface
    state from two files it maintains under $HERMES_HOME:

    * ``cron/jobs.json`` — authoritative job definitions hermes-agent actually
      uses at runtime.  Present iff the scheduler has been configured.
    * ``scheduler_state.json`` — runtime state written after each scheduler
      loop tick (last_run_at, failed_jobs).

    Both files are read without doing work in the request path.
    """
    import json

    hermes_home: Path = getattr(paths, "hermes_home", Path("/data/.hermes"))

    # cron/jobs.json is the source of truth for what jobs are scheduled.
    job_count: int | None = None
    try:
        cron_jobs_file = Path(hermes_home) / "cron" / "jobs.json"
        if cron_jobs_file.exists():
            data = json.loads(cron_jobs_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                job_count = len(data)
            elif isinstance(data, dict):
                job_count = len(data)
    except (OSError, ValueError):
        pass

    # scheduler_state.json carries runtime metrics written by the scheduler loop.
    last_run_at: str | None = None
    failed_jobs: int | None = None
    state = "unknown"
    try:
        state_file = Path(hermes_home) / "scheduler_state.json"
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                last_run_at = data.get("last_run_at")
                fj = data.get("failed_jobs")
                if isinstance(fj, int):
                    failed_jobs = fj
                state = "ready"
    except (OSError, ValueError):
        pass

    # cron/jobs.json present → scheduler has jobs even if state file is absent/stale.
    if job_count is not None and state == "unknown":
        state = "configured"

    return {
        "state": state,
        "last_run_at": last_run_at,
        "failed_jobs": failed_jobs,
        "job_count": job_count,
    }


def _readiness_to_payload(readiness: Any) -> dict[str, Any]:
    if readiness is None:
        return {}
    if hasattr(readiness, "readiness"):
        return {k: v.as_dict() for k, v in readiness.readiness.items()}
    if isinstance(readiness, dict):
        return dict(readiness)
    return {}


def _versions_payload(readiness: Any) -> dict[str, Any]:
    if readiness is None:
        return {}
    if hasattr(readiness, "versions"):
        return dict(readiness.versions)
    return {}


def _gateway_snapshot(app_state: Any) -> dict[str, Any]:
    gw = getattr(app_state, "gateway", None)
    if gw is None:
        return {
            "state": "disabled",
            "platform": None,
            "connection": "not_configured",
        }
    snap = gw.snapshot()
    return {
        "state": snap.get("state", "unknown"),
        "platform": snap.get("platform"),
        "connection": snap.get("connection", "unknown"),
    }


def _webui_snapshot(app_state: Any) -> dict[str, Any]:
    wb = getattr(app_state, "webui", None)
    if wb is None:
        return {"state": "disabled", "pid": None}
    snap = wb.snapshot()
    return {
        "state": snap.get("state", "down"),
        "pid": snap.get("pid"),
    }


def _compose_status(
    *,
    storage: dict[str, Any],
    readiness: Any,
    webui: dict[str, Any],
) -> str:
    if not storage.get("data_writable", False):
        return "down"
    degraded = False
    if readiness is not None and hasattr(readiness, "any_intended_not_ready"):
        if readiness.any_intended_not_ready():
            degraded = True
    elif readiness is not None and isinstance(readiness, dict):
        rd = readiness.get("readiness") or readiness
        for row in rd.values() if isinstance(rd, dict) else []:
            if isinstance(row, dict) and row.get("intended") and not row.get("ready"):
                degraded = True
                break
    if webui.get("state") not in {"ready", "disabled"}:
        degraded = True
    return "degraded" if degraded else "ok"


def _build_payload(request: Request) -> dict[str, Any]:
    state = request.app.state
    paths = getattr(state, "paths", None)
    readiness = getattr(state, "readiness", None)

    storage = _storage_block(paths)
    gateway = _gateway_snapshot(state)
    webui = _webui_snapshot(state)
    memory = _memory_block(readiness, paths)
    scheduler = _scheduler_block(paths)

    status = _compose_status(storage=storage, readiness=readiness, webui=webui)

    payload: dict[str, Any] = {
        "status": status,
        "components": {
            "control_plane": {"state": "ready"},
            "webui": webui,
            "gateway": gateway,
            "scheduler": scheduler,
            "storage": storage,
            "memory": memory,
        },
        "readiness": _readiness_to_payload(readiness),
        "versions": _versions_payload(readiness),
    }
    return payload


async def health_live(request: Request) -> JSONResponse:  # noqa: ARG001
    """Cheap liveness probe. Does not touch subprocess/network state."""
    return JSONResponse({"status": "alive"})


async def health_full(request: Request) -> JSONResponse:
    """Full structured health payload. Always 200 (status is in body)."""
    payload = _build_payload(request)
    return JSONResponse(payload)


async def health_ready(request: Request) -> JSONResponse:
    """Composite readiness — non-200 when degraded/down."""
    payload = _build_payload(request)
    code = 200 if payload.get("status") == "ok" else 503
    return JSONResponse(payload, status_code=code)


# Legacy plaintext route — kept available for callers; mounted under a
# different path by app.py when desired. Not registered in `routes()` since
# the new structured endpoints replace it.
async def health_plain(request: Request) -> PlainTextResponse:  # noqa: ARG001
    return PlainTextResponse("ok")


def routes() -> list[Route]:
    """Return the Starlette routes that compose the health surface."""
    return [
        Route("/health", health_full, methods=["GET"]),
        Route("/health/live", health_live, methods=["GET"]),
        Route("/health/ready", health_ready, methods=["GET"]),
    ]


__all__ = [
    "routes",
    "health_full",
    "health_live",
    "health_ready",
    "health_plain",
]


# Silence ruff "imported but unused" for os import.
_ = os
