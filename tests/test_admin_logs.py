"""Tests for the admin Logs viewer (ring buffers + endpoints)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

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
    response = await client.post(
        "/admin/login", data={"password": password}, follow_redirects=False
    )
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
