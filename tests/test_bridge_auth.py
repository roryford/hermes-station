"""Unit tests for the webui session bridge auth.

Pattern: ``httpx.ASGITransport`` against a tiny fake-webui ASGI app, no
external mock library. Mirrors the approach used in
``tests/test_admin_endpoints.py`` (which uses ASGITransport against the real
station app).

The fake webui is mounted on the bridge's pooled ``AsyncClient`` so the bridge
calls reach our fixture instead of the real loopback port.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from hermes_station.admin.bridge_auth import verify_webui_session


# ─────────────────────────────────────────────────────────── fake webui app


RequestCounter = dict[str, int]


def _make_fake_webui(
    handler: Callable[[Request], Awaitable[Response]],
    counter: RequestCounter,
) -> Starlette:
    async def _wrapped(request: Request) -> Response:
        counter["calls"] += 1
        return await handler(request)

    return Starlette(routes=[Route("/api/auth/status", _wrapped, methods=["GET"])])


def _build_bridge_request(
    app: Starlette,
    cookie: str | None,
    bridge_client: httpx.AsyncClient | None,
) -> Request:
    """Construct a Starlette Request with the given Cookie header and an app
    whose ``state.bridge_http_client`` points at ``bridge_client``."""
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("utf-8")))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/admin/api/ping",
        "raw_path": b"/admin/api/ping",
        "query_string": b"",
        "headers": headers,
        "app": app,
    }
    request = Request(scope)
    return request


def _build_station_app(bridge_client: httpx.AsyncClient | None) -> Starlette:
    app = Starlette(routes=[])
    app.state.bridge_http_client = bridge_client
    return app


def _bridge_client_for(fake_webui: Starlette) -> httpx.AsyncClient:
    """An httpx.AsyncClient that routes calls to 127.0.0.1:8788 into the fake."""
    transport = httpx.ASGITransport(app=fake_webui)
    return httpx.AsyncClient(transport=transport, timeout=2.0)


# ─────────────────────────────────────────────────────────── happy path


async def test_logged_in_true_returns_true() -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return JSONResponse({"auth_enabled": True, "logged_in": True})

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, "hermes_session=abc123", bridge_client)
        assert await verify_webui_session(request) is True
    assert counter["calls"] == 1


async def test_logged_in_false_returns_false() -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return JSONResponse({"auth_enabled": True, "logged_in": False})

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, "hermes_session=abc123", bridge_client)
        assert await verify_webui_session(request) is False
    assert counter["calls"] == 1


# ─────────────────────────────────────────────────────────── short-circuit


async def test_missing_cookie_short_circuits() -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return JSONResponse({"logged_in": True})

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, None, bridge_client)
        assert await verify_webui_session(request) is False
    assert counter["calls"] == 0


async def test_empty_cookie_short_circuits() -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return JSONResponse({"logged_in": True})

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, "", bridge_client)
        assert await verify_webui_session(request) is False
    assert counter["calls"] == 0


async def test_cookie_without_hermes_session_short_circuits() -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return JSONResponse({"logged_in": True})

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, "other_cookie=value; another=stuff", bridge_client)
        assert await verify_webui_session(request) is False
    assert counter["calls"] == 0


# ─────────────────────────────────────────────────────────── failure modes


SECRET_COOKIE = "hermes_session=SUPER_SECRET_VALUE_XYZ"


def _assert_no_raw_cookie(caplog: pytest.LogCaptureFixture) -> None:
    assert "SUPER_SECRET_VALUE_XYZ" not in caplog.text, "raw cookie value leaked into logs"


async def test_4xx_returns_false_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return PlainTextResponse("forbidden", status_code=403)

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        with caplog.at_level(logging.INFO, logger="hermes_station.admin.bridge_auth"):
            assert await verify_webui_session(request) is False
    assert counter["calls"] == 1
    assert any(rec.levelno >= logging.INFO for rec in caplog.records)
    _assert_no_raw_cookie(caplog)


async def test_5xx_returns_false_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return PlainTextResponse("boom", status_code=503)

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        with caplog.at_level(logging.INFO, logger="hermes_station.admin.bridge_auth"):
            assert await verify_webui_session(request) is False
    assert counter["calls"] == 1
    assert any(rec.levelno >= logging.INFO for rec in caplog.records)
    _assert_no_raw_cookie(caplog)


async def test_timeout_returns_false_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Timeout path: bridge surfaces TimeoutException as False (no raise) and logs.

    httpx's per-call timeout isn't honored by ASGITransport for an awaited
    coroutine inside the fake app, so we simulate the timeout by stubbing the
    pooled client's ``get`` method to raise ``httpx.ReadTimeout`` directly —
    which is exactly what the real loopback would raise when webui doesn't
    respond inside the configured 2s budget.
    """

    class _TimingOutClient:
        async def get(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            raise httpx.ReadTimeout("simulated timeout")

    app = _build_station_app(_TimingOutClient())  # type: ignore[arg-type]
    request = _build_bridge_request(app, SECRET_COOKIE, None)
    with caplog.at_level(logging.INFO, logger="hermes_station.admin.bridge_auth"):
        assert await verify_webui_session(request) is False
    assert any(rec.levelno >= logging.INFO for rec in caplog.records)
    _assert_no_raw_cookie(caplog)


async def test_non_json_returns_false_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return PlainTextResponse("not json at all", status_code=200)

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        with caplog.at_level(logging.INFO, logger="hermes_station.admin.bridge_auth"):
            assert await verify_webui_session(request) is False
    assert any(rec.levelno >= logging.INFO for rec in caplog.records)
    _assert_no_raw_cookie(caplog)


async def test_json_missing_logged_in_returns_false_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(_request: Request) -> Response:
        return Response(
            json.dumps({"auth_enabled": True}),
            status_code=200,
            media_type="application/json",
        )

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        with caplog.at_level(logging.INFO, logger="hermes_station.admin.bridge_auth"):
            assert await verify_webui_session(request) is False
    assert any(rec.levelno >= logging.INFO for rec in caplog.records)
    _assert_no_raw_cookie(caplog)


# ─────────────────────────────────────────────────────────── concurrency


async def test_ten_concurrent_verifications() -> None:
    counter: RequestCounter = {"calls": 0}

    async def handler(request: Request) -> Response:
        # Echo whether the inbound cookie says "yes" via a query-like substring.
        cookie = request.headers.get("cookie", "")
        return JSONResponse({"logged_in": "yes" in cookie})

    fake = _make_fake_webui(handler, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)

        async def verify(yes: bool) -> bool:
            cookie = f"hermes_session={'yes' if yes else 'no'}"
            request = _build_bridge_request(app, cookie, bridge_client)
            return await verify_webui_session(request)

        results = await asyncio.gather(*[verify(i % 2 == 0) for i in range(10)])
    expected = [i % 2 == 0 for i in range(10)]
    assert results == expected
    assert counter["calls"] == 10


# ─────────────────────────────────────────────────────────── safety


async def test_missing_bridge_client_returns_false(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _build_station_app(None)
    request = _build_bridge_request(app, SECRET_COOKIE, None)
    with caplog.at_level(logging.INFO, logger="hermes_station.admin.bridge_auth"):
        assert await verify_webui_session(request) is False
    _assert_no_raw_cookie(caplog)


# ─────────────────────────────────────────────────────────── counters


async def test_failure_counters_increment_per_bucket() -> None:
    """Each failure path bumps exactly the counter for its bucket."""
    from hermes_station.admin import bridge_auth as ba

    ba.reset_bridge_failures_total()

    # http_4xx
    counter: RequestCounter = {"calls": 0}

    async def h_403(_r: Request) -> Response:
        return PlainTextResponse("forbidden", status_code=403)

    fake = _make_fake_webui(h_403, counter)
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        await verify_webui_session(request)

    # http_5xx
    async def h_503(_r: Request) -> Response:
        return PlainTextResponse("boom", status_code=503)

    fake = _make_fake_webui(h_503, {"calls": 0})
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        await verify_webui_session(request)

    # malformed_json
    async def h_text(_r: Request) -> Response:
        return PlainTextResponse("not json", status_code=200)

    fake = _make_fake_webui(h_text, {"calls": 0})
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        await verify_webui_session(request)

    # missing_field
    async def h_missing(_r: Request) -> Response:
        return Response(
            json.dumps({"auth_enabled": True}),
            status_code=200,
            media_type="application/json",
        )

    fake = _make_fake_webui(h_missing, {"calls": 0})
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        await verify_webui_session(request)

    # timeout
    class _TimingOutClient:
        async def get(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            raise httpx.ReadTimeout("simulated timeout")

    app = _build_station_app(_TimingOutClient())  # type: ignore[arg-type]
    request = _build_bridge_request(app, SECRET_COOKIE, None)
    await verify_webui_session(request)

    snapshot = ba.get_bridge_failures_total()
    assert snapshot == {
        "timeout": 1,
        "http_4xx": 1,
        "http_5xx": 1,
        "malformed_json": 1,
        "missing_field": 1,
    }

    # And success paths must NOT touch counters.
    ba.reset_bridge_failures_total()

    async def h_ok(_r: Request) -> Response:
        return JSONResponse({"auth_enabled": True, "logged_in": True})

    fake = _make_fake_webui(h_ok, {"calls": 0})
    async with _bridge_client_for(fake) as bridge_client:
        app = _build_station_app(bridge_client)
        request = _build_bridge_request(app, SECRET_COOKIE, bridge_client)
        assert await verify_webui_session(request) is True

    assert ba.get_bridge_failures_total() == {
        "timeout": 0,
        "http_4xx": 0,
        "http_5xx": 0,
        "malformed_json": 0,
        "missing_field": 0,
    }


def test_get_bridge_failures_total_returns_copy() -> None:
    """Snapshot must not be the internal dict (mutation safety)."""
    from hermes_station.admin import bridge_auth as ba

    ba.reset_bridge_failures_total()
    snap = ba.get_bridge_failures_total()
    snap["timeout"] = 999
    assert ba.get_bridge_failures_total()["timeout"] == 0
