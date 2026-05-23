"""Tests for the pilot smoketest SSE endpoint.

POST /admin/api/pilot/smoketest

Covers:
- Flag-off returns 404.
- Unauthenticated returns 401.
- Cross-origin POST rejected (403 CSRF defense).
- Happy-path: streaming response has correct content-type and delivers
  per-check events followed by a ``__done__`` summary event.
- Failure mode: when the provider check fails (bad key), the event stream
  contains a ``fail`` status for ``provider`` and the ``__done__`` event
  reports at least one failure.
- Route is POST-only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


async def _login(client: httpx.AsyncClient, password: str) -> None:
    r = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert r.status_code == 302, r.text


def _parse_sse_events(body: bytes) -> list[dict[str, Any]]:
    """Parse ``data: {...}`` lines from a raw SSE response body."""
    events: list[dict[str, Any]] = []
    for line in body.decode().splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ── flag off ─────────────────────────────────────────────────────────────────


async def test_smoketest_flag_off_returns_404(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post("/admin/api/pilot/smoketest")

    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


# ── unauthenticated ───────────────────────────────────────────────────────────


async def test_smoketest_unauthenticated_returns_401(fake_data_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/api/pilot/smoketest")

    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


# ── CSRF defense ──────────────────────────────────────────────────────────────


async def test_smoketest_cross_origin_post_rejected(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/api/pilot/smoketest",
            headers={"Origin": "http://evil.example.com"},
        )

    assert r.status_code == 403
    data = r.json()
    assert data["ok"] is False
    assert "cross-origin" in data["error"].lower()


async def test_smoketest_same_origin_post_accepted(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """Matching Origin passes the CSRF check and the stream starts."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app
    from hermes_station.admin import pilot_smoketest as _mod

    # Stub all checks to return immediately so the test doesn't hit the network.
    _pass = {"name": "x", "status": "pass", "detail": "ok", "fix": "", "label": "X"}
    _skip = {"name": "x", "status": "skip", "detail": "n/a", "fix": "", "label": "X"}

    async def _fast_pass(*_a, **_kw) -> dict:
        return _pass

    async def _fast_skip(*_a, **_kw) -> dict:
        return _skip

    patches = [
        patch.object(_mod, "_test_storage", side_effect=_fast_pass),
        patch.object(_mod, "_test_provider", side_effect=_fast_pass),
        patch.object(_mod, "_test_gateway", side_effect=_fast_skip),
        patch.object(_mod, "_test_github_mcp", side_effect=_fast_skip),
        patch.object(_mod, "_test_web_search", side_effect=_fast_skip),
        patch.object(_mod, "_test_image_gen", side_effect=_fast_skip),
        patch.object(_mod, "_test_browser_backend", side_effect=_fast_skip),
        patch.object(_mod, "_test_plugin_registry", side_effect=_fast_skip),
        patch.object(_mod, "_test_mcp_urls", side_effect=_fast_skip),
    ]

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    with pytest.MonkeyPatch().context() as _mp:
        pass  # just a scope holder; patches are applied below

    for p in patches:
        p.start()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            r = await client.post(
                "/admin/api/pilot/smoketest",
                headers={"Origin": "http://test"},
            )
    finally:
        for p in patches:
            p.stop()

    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")


# ── happy path ────────────────────────────────────────────────────────────────


async def test_smoketest_happy_path_streams_events(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """Happy path: each check emits a pending + result event, then __done__."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app
    from hermes_station.admin import pilot_smoketest as _mod

    _pass_result = {"name": "x", "status": "pass", "detail": "ok", "fix": "", "label": "X"}
    _skip_result = {"name": "x", "status": "skip", "detail": "n/a", "fix": "", "label": "X"}

    async def _fast_pass(*_a, **_kw) -> dict:
        return _pass_result

    async def _fast_skip(*_a, **_kw) -> dict:
        return _skip_result

    patches = [
        patch.object(_mod, "_test_storage", side_effect=_fast_pass),
        patch.object(_mod, "_test_provider", side_effect=_fast_pass),
        patch.object(_mod, "_test_gateway", side_effect=_fast_skip),
        patch.object(_mod, "_test_github_mcp", side_effect=_fast_skip),
        patch.object(_mod, "_test_web_search", side_effect=_fast_skip),
        patch.object(_mod, "_test_image_gen", side_effect=_fast_skip),
        patch.object(_mod, "_test_browser_backend", side_effect=_fast_skip),
        patch.object(_mod, "_test_plugin_registry", side_effect=_fast_skip),
        patch.object(_mod, "_test_mcp_urls", side_effect=_fast_skip),
    ]

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    for p in patches:
        p.start()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            r = await client.post("/admin/api/pilot/smoketest")
    finally:
        for p in patches:
            p.stop()

    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")

    events = _parse_sse_events(r.content)
    assert events, "Expected at least one SSE event"

    # Must contain at least one pending event.
    pending = [e for e in events if e.get("status") == "pending"]
    assert pending, "Expected at least one pending event"

    # Must contain a __done__ event.
    done = [e for e in events if e.get("check") == "__done__"]
    assert len(done) == 1, f"Expected exactly one __done__ event, got {done}"
    assert done[0]["status"] in ("pass", "fail")

    # Check that each check name appears at least once (as a non-pending event).
    _EXPECTED_CHECKS = {
        "storage", "provider", "gateway", "github_mcp", "web_search",
        "image_gen", "browser_backend", "plugin_registry", "mcp_urls",
    }
    result_checks = {e["check"] for e in events if e.get("check") != "__done__" and e.get("status") != "pending"}
    assert _EXPECTED_CHECKS == result_checks, f"Missing checks: {_EXPECTED_CHECKS - result_checks}"

    # All pass/skip → __done__ status should be "pass".
    assert done[0]["status"] == "pass", f"__done__ status should be pass, got {done[0]}"


# ── failure mode: bad provider key ───────────────────────────────────────────


async def test_smoketest_provider_fail_reflected_in_stream(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """When provider check returns fail, the stream event carries fail status
    and the __done__ event reports a failure."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app
    from hermes_station.admin import pilot_smoketest as _mod

    _fail_provider = {
        "name": "provider",
        "status": "fail",
        "detail": 'No credential found for "openrouter".',
        "fix": "Add the API key in Settings.",
        "label": "Provider",
    }
    _pass_result = {"name": "x", "status": "pass", "detail": "ok", "fix": "", "label": "X"}
    _skip_result = {"name": "x", "status": "skip", "detail": "n/a", "fix": "", "label": "X"}

    async def _provider_fail(*_a, **_kw) -> dict:
        return _fail_provider

    async def _fast_pass(*_a, **_kw) -> dict:
        return _pass_result

    async def _fast_skip(*_a, **_kw) -> dict:
        return _skip_result

    patches = [
        patch.object(_mod, "_test_storage", side_effect=_fast_pass),
        patch.object(_mod, "_test_provider", side_effect=_provider_fail),
        patch.object(_mod, "_test_gateway", side_effect=_fast_skip),
        patch.object(_mod, "_test_github_mcp", side_effect=_fast_skip),
        patch.object(_mod, "_test_web_search", side_effect=_fast_skip),
        patch.object(_mod, "_test_image_gen", side_effect=_fast_skip),
        patch.object(_mod, "_test_browser_backend", side_effect=_fast_skip),
        patch.object(_mod, "_test_plugin_registry", side_effect=_fast_skip),
        patch.object(_mod, "_test_mcp_urls", side_effect=_fast_skip),
    ]

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    for p in patches:
        p.start()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            r = await client.post("/admin/api/pilot/smoketest")
    finally:
        for p in patches:
            p.stop()

    assert r.status_code == 200
    events = _parse_sse_events(r.content)

    # Find the provider result event (non-pending).
    provider_results = [
        e for e in events if e.get("check") == "provider" and e.get("status") != "pending"
    ]
    assert len(provider_results) == 1
    assert provider_results[0]["status"] == "fail"
    assert "credential" in provider_results[0]["detail"].lower()

    # __done__ should reflect at least one failure.
    done = [e for e in events if e.get("check") == "__done__"]
    assert len(done) == 1
    assert done[0]["status"] == "fail"
    assert "1 failed" in done[0]["detail"]


# ── route table check ─────────────────────────────────────────────────────────


def test_smoketest_route_is_post_only() -> None:
    """Only POST is wired up — no GET-triggered side effects."""
    from hermes_station.admin.routes import admin_routes
    from starlette.routing import Route

    for route in admin_routes():
        if isinstance(route, Route) and route.path == "/admin/api/pilot/smoketest":
            assert set(route.methods or set()) == {"POST"}, (
                f"smoketest route should be POST-only, got {route.methods}"
            )
            return
    raise AssertionError("smoketest route not registered in admin_routes()")
