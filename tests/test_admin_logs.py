"""Tests for the admin Logs viewer (ring buffers + endpoints)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from hermes_station.logs import (
    BUFFERS,
    LogBuffer,
    RingBufferHandler,
    STATION_LOGS,
    attach_station_handler,
)


# ───────────────────────────────────────────────────────────── LogBuffer unit


def test_log_buffer_append_and_tail() -> None:
    buf = LogBuffer(maxlen=10)
    for i in range(5):
        buf.append(f"line-{i}")
    assert buf.tail(3) == ["line-2", "line-3", "line-4"]
    assert buf.tail(100) == [f"line-{i}" for i in range(5)]
    assert buf.tail(0) == []


def test_log_buffer_respects_maxlen() -> None:
    buf = LogBuffer(maxlen=3)
    for i in range(10):
        buf.append(f"line-{i}")
    assert len(buf) == 3
    assert buf.tail(10) == ["line-7", "line-8", "line-9"]


def test_ring_buffer_handler_formats_and_appends() -> None:
    buf = LogBuffer(maxlen=5)
    handler = RingBufferHandler(buf)
    log = logging.getLogger("hermes_station._test_logs_handler")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        log.info("hello %s", "world")
    finally:
        log.removeHandler(handler)
    tail = buf.tail(1)
    assert len(tail) == 1
    assert "hello world" in tail[0]
    assert "INFO" in tail[0]


# ───────────────────────────────────────────────────────────────── endpoints


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


async def test_api_logs_requires_admin(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/api/logs/station")
    assert response.status_code == 401


async def test_api_logs_returns_station_lines(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    attach_station_handler()
    marker = "logs-test-marker-xyzzy"
    logging.getLogger("hermes_station.test").error(marker)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/api/logs/station")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "station"
    assert payload["count"] == len(payload["lines"])
    assert any(marker in line for line in payload["lines"])


async def test_api_logs_unknown_source_400(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/api/logs/bogus")
    assert response.status_code == 400


async def test_api_logs_limit_clamped(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/api/logs/webui?limit=9999")
    assert response.status_code == 200
    assert response.json()["count"] <= 500


async def test_logs_fragment_unauth_returns_401(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/_partial/logs/station")
    assert response.status_code == 401


def test_buffers_registry_has_three_sources() -> None:
    assert set(BUFFERS.keys()) == {"station", "gateway", "webui"}
    assert BUFFERS["station"] is STATION_LOGS


# ---------------------------------------------------------------------------
# _parse_limit helper unit tests (from test_coverage_boost.py)
# ---------------------------------------------------------------------------


def test_parse_limit_default_when_none() -> None:
    from hermes_station.admin.htmx_logs import _parse_limit

    assert _parse_limit(None) == 200


def test_parse_limit_default_when_invalid() -> None:
    from hermes_station.admin.htmx_logs import _parse_limit

    assert _parse_limit("abc") == 200


def test_parse_limit_clamped_to_max() -> None:
    from hermes_station.admin.htmx_logs import _parse_limit

    assert _parse_limit("9999") == 500


def test_parse_limit_clamped_to_min() -> None:
    from hermes_station.admin.htmx_logs import _parse_limit

    assert _parse_limit("-5") == 1


def test_parse_limit_normal() -> None:
    from hermes_station.admin.htmx_logs import _parse_limit

    assert _parse_limit("50") == 50


# ---------------------------------------------------------------------------
# logs_page auth guard (lines 38-41)
# ---------------------------------------------------------------------------


async def test_logs_page_requires_admin(fake_data_dir: Path, admin_password: str) -> None:
    """Unauthenticated GET /admin/logs redirects to login (covers lines 38-41)."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/logs", follow_redirects=False)
    assert response.status_code == 302
    assert "login" in response.headers["location"]


async def test_logs_page_renders_after_login(fake_data_dir: Path, admin_password: str) -> None:
    """Authenticated GET /admin/logs renders the page (covers lines 41-49)."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/logs")
    assert response.status_code == 200
    body = response.text
    assert "Logs" in body


# ---------------------------------------------------------------------------
# _parse_line — non-JSON and non-dict fallback (lines 54-59)
# ---------------------------------------------------------------------------


def test_parse_line_non_json_falls_back() -> None:
    """Plain text line falls back gracefully (covers lines 54-59)."""
    from hermes_station.admin.htmx_logs import _parse_line

    result = _parse_line("this is plain text, not JSON")
    assert result["level"] == "plain"
    assert result["message"] == "this is plain text, not JSON"
    assert result["raw"] == "this is plain text, not JSON"
    assert result["ts"] == ""
    assert result["component"] == ""


def test_parse_line_non_dict_json_falls_back() -> None:
    """JSON that isn't a dict (e.g. a list) falls back to plain (covers lines 56-59)."""
    from hermes_station.admin.htmx_logs import _parse_line

    result = _parse_line('["not", "a", "dict"]')
    assert result["level"] == "plain"
    assert result["message"] == '["not", "a", "dict"]'


def test_parse_line_valid_json_log() -> None:
    """Valid JSON log dict returns parsed fields (covers lines 60-67)."""
    import json

    from hermes_station.admin.htmx_logs import _parse_line

    raw = json.dumps({"ts": "2024-01-01T00:00:00", "level": "ERROR", "component": "core", "message": "boom"})
    result = _parse_line(raw)
    assert result["level"] == "ERROR"
    assert result["ts"] == "2024-01-01T00:00:00"
    assert result["component"] == "core"
    assert result["message"] == "boom"


def test_parse_line_uses_msg_fallback() -> None:
    """JSON with 'msg' key instead of 'message' still parses (covers line 65)."""
    import json

    from hermes_station.admin.htmx_logs import _parse_line

    raw = json.dumps({"ts": "t", "level": "info", "msg": "alt message"})
    result = _parse_line(raw)
    assert result["message"] == "alt message"


# ---------------------------------------------------------------------------
# logs_fragment — unknown source (lines 73-79) and successful render
# ---------------------------------------------------------------------------


async def test_logs_fragment_unknown_source_400(fake_data_dir: Path, admin_password: str) -> None:
    """Authenticated request for unknown source returns 400 (covers lines 73-75)."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/logs/not-a-source")
    assert response.status_code == 400
    assert response.json()["error"] == "unknown source"


async def test_logs_fragment_renders_html(fake_data_dir: Path, admin_password: str) -> None:
    """Authenticated request for valid source renders HTML fragment (covers lines 76-83)."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/logs/station")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


async def test_logs_fragment_with_limit_param(fake_data_dir: Path, admin_password: str) -> None:
    """Fragment endpoint respects custom limit query param (covers lines 76-83)."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/logs/gateway?limit=10")
    assert response.status_code == 200
