"""HTTP proxy forwarding `/` (and anything not handled by /admin or /health)
to the hermes-webui subprocess on its internal port.

Pragmatic alternative to mounting hermes-webui as an ASGI sub-app, since
hermes-webui is built on stdlib `http.server` and isn't mountable. We forward
HTTP at the process boundary instead.

Notable behaviors:
- Streams responses so SSE (text/event-stream) works for live chat.
- Strips hop-by-hop headers per RFC 7230.
- Strips our own admin session cookie before forwarding, so a logged-in admin
  session doesn't leak into WebUI's cookie jar.
- Drops the upstream `content-encoding` header because httpx returns the
  decompressed body — passing the original encoding back would confuse the
  client.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

logger = logging.getLogger("hermes_station.proxy")

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

_OUR_COOKIES = frozenset({"hermes_station_admin"})


def _filter_request_headers(headers: "dict[str, str] | httpx.Headers") -> dict[str, str]:
    out: dict[str, str] = {}
    items = headers.items() if hasattr(headers, "items") else headers
    for key, value in items:
        lower = key.lower()
        if lower in _HOP_BY_HOP or lower == "host":
            continue
        out[key] = value
    return out


def _strip_our_cookies(cookie_header: str | None) -> str:
    if not cookie_header:
        return ""
    keep: list[str] = []
    for raw in cookie_header.split(";"):
        name = raw.split("=", 1)[0].strip()
        if name in _OUR_COOKIES:
            continue
        if raw.strip():
            keep.append(raw.strip())
    return "; ".join(keep)


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in _HOP_BY_HOP:
            continue
        if lower in {"content-encoding", "content-length"}:
            continue
        out[key] = value
    return out


async def proxy_to_webui(request: Request) -> Response:
    webui = request.app.state.webui
    client: httpx.AsyncClient = request.app.state.proxy_client

    upstream_url = httpx.URL(
        scheme="http",
        host=webui.INTERNAL_HOST,
        port=webui.INTERNAL_PORT,
        path=request.url.path,
        query=request.url.query.encode("utf-8"),
    )

    headers = _filter_request_headers(request.headers)
    cookie = _strip_our_cookies(request.headers.get("cookie"))
    if cookie:
        headers["cookie"] = cookie
    elif "cookie" in headers:
        del headers["cookie"]

    body = await request.body()

    try:
        upstream_request = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body if body else None,
        )
        upstream = await client.send(upstream_request, stream=True)
    except (httpx.HTTPError, httpx.RequestError) as exc:
        logger.warning("proxy upstream error: %s", exc)
        return Response(b"upstream WebUI unavailable", status_code=502)

    async def _stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _stream(),
        status_code=upstream.status_code,
        headers=_response_headers(upstream),
    )
