"""SSE-streamed smoketest endpoint for the pilot admin extension.

POST /admin/api/pilot/smoketest
  - Auth: dual-cookie (webui session or legacy station admin cookie)
  - CSRF: same-origin / absent Origin check (mirrors gateway restart posture)
  - Returns: text/event-stream, one JSON event per check, then a ``done`` event

Event shape::

    data: {"check": "provider", "status": "pending", "detail": ""}
    data: {"check": "provider", "status": "pass", "detail": "openrouter API is reachable (HTTP 200)."}
    ...
    data: {"check": "__done__", "status": "pass", "detail": "3 passed, 0 failed"}

This module ports the logic from ``hermes_station/admin/smoketest.py`` into the
JSON / SSE pattern used by all pilot endpoints.  The underlying test helpers are
re-used directly — no duplication.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from hermes_station.admin.auth import is_authenticated
from hermes_station.admin.bridge_auth import verify_webui_session
from hermes_station.admin.smoketest import (
    _test_browser_backend,
    _test_gateway,
    _test_github_mcp,
    _test_image_gen,
    _test_mcp_urls,
    _test_plugin_registry,
    _test_provider,
    _test_storage,
    _test_web_search,
)
from hermes_station.config import Paths, load_env_file, load_yaml_config, pilot_admin_extension_enabled

logger = logging.getLogger(__name__)


def _sse_event(data: dict[str, Any]) -> str:
    """Format a single SSE ``data:`` line."""
    return f"data: {json.dumps(data)}\n\n"


async def _run_checks_stream(request: Request) -> AsyncGenerator[str, None]:
    """Yield SSE events for each smoketest check, then a ``__done__`` summary."""
    paths: Paths = request.app.state.paths
    config = load_yaml_config(paths.config_path)
    env = load_env_file(paths.env_path)
    gateway = getattr(request.app.state, "gateway", None)

    # The ordered list of (check_name, coroutine) pairs.
    # We yield a "pending" event before each check so the UI can show
    # a spinner immediately, then a pass/fail event when the check resolves.
    checks: list[tuple[str, Any]] = [
        ("storage", _test_storage(paths)),
        ("provider", _test_provider(config, env)),
        ("gateway", _test_gateway(gateway, config)),
        ("github_mcp", _test_github_mcp(config, env)),
        ("web_search", _test_web_search(config, env)),
        ("image_gen", _test_image_gen(config, env)),
        ("browser_backend", _test_browser_backend(env)),
        ("plugin_registry", _test_plugin_registry()),
        ("mcp_urls", _test_mcp_urls(config, env)),
    ]

    passed = 0
    failed = 0

    for check_name, coro in checks:
        # Emit pending before running.
        yield _sse_event({"check": check_name, "status": "pending", "detail": ""})
        try:
            result = await coro
        except Exception as exc:  # noqa: BLE001
            logger.exception("smoketest check %r raised unexpectedly: %s", check_name, exc)
            result = {
                "name": check_name,
                "status": "fail",
                "detail": f"Unexpected error: {exc}",
            }
        status = result.get("status", "fail")
        if status == "pass":
            passed += 1
        elif status == "fail":
            failed += 1
        # skip counts neither.
        yield _sse_event(
            {
                "check": check_name,
                "status": status,
                "detail": result.get("detail", ""),
                "fix": result.get("fix", ""),
                "label": result.get("label", check_name),
            }
        )

    # Summary done event.
    done_status = "pass" if failed == 0 else "fail"
    summary = f"{passed} passed, {failed} failed"
    if (passed + failed) < len(checks):
        skipped = len(checks) - passed - failed
        summary += f", {skipped} skipped"
    yield _sse_event({"check": "__done__", "status": done_status, "detail": summary})


def _same_origin_or_missing(request: Request) -> bool:
    """CSRF defense: mirrors the posture used by gateway restart."""
    from urllib.parse import urlsplit

    request_host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    if origin:
        try:
            origin_host = urlsplit(origin).netloc
        except ValueError:
            return False
        return bool(origin_host) and origin_host == request_host
    referer = request.headers.get("referer", "")
    if referer:
        try:
            referer_host = urlsplit(referer).netloc
        except ValueError:
            return False
        return bool(referer_host) and referer_host == request_host
    return True


async def api_pilot_smoketest(request: Request) -> Response:
    """POST /admin/api/pilot/smoketest — run all connectivity checks, stream SSE results."""
    if not pilot_admin_extension_enabled():
        return JSONResponse({"error": "not_found"}, status_code=404)

    if not await verify_webui_session(request):
        if not is_authenticated(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not _same_origin_or_missing(request):
        logger.warning(
            "pilot smoketest: cross-origin POST rejected (origin=%r host=%r)",
            request.headers.get("origin", ""),
            request.headers.get("host", ""),
        )
        return JSONResponse({"ok": False, "error": "cross-origin request rejected"}, status_code=403)

    return StreamingResponse(
        _run_checks_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering
        },
    )
