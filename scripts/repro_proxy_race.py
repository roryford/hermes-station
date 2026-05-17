#!/usr/bin/env python3
"""Repro for the HTTP/1.1 keep-alive race that caused 502s in production.

Simulates the exact failure mode logged at 2026-05-17T00:17:40:
    proxy upstream error: Server disconnected without sending a response.

The upstream WebUI closes idle keep-alive connections after ~5s. httpx
keeps connections in its pool for the same window. When the proxy picks
a pooled connection that the server has just closed, httpx raises
`RemoteProtocolError`. Without retry, the proxy returns 502 even though
the upstream is healthy.

Usage:
    python scripts/repro_proxy_race.py
Exit 0 = proxy retried and recovered. Exit 1 = no retry, returned 502.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.proxy import proxy_to_webui


class _FakeWebUI:
    INTERNAL_HOST = "127.0.0.1"
    INTERNAL_PORT = 8788


async def main() -> int:
    upstream_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        upstream_calls["n"] += 1
        if upstream_calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    async def route(request):  # type: ignore[no-untyped-def]
        return await proxy_to_webui(request)

    app = Starlette(routes=[Route("/{p:path}", route, methods=["GET"])])
    app.state.webui = _FakeWebUI()
    app.state.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://station.test"
    ) as client:
        resp = await client.get("/api/sessions")

    await app.state.proxy_client.aclose()

    print(f"downstream status = {resp.status_code}")
    print(f"upstream calls    = {upstream_calls['n']}")
    if resp.status_code == 200 and upstream_calls["n"] == 2:
        print("PASS: proxy retried after RemoteProtocolError and recovered")
        return 0
    print("FAIL: proxy did not retry (production behavior — returns 502)")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
