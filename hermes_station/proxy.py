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
- Injects X-Forwarded-Host / X-Real-Host / X-Forwarded-Proto so hermes-webui's
  CSRF check (which compares browser Origin against the request's Host family)
  sees the public hostname instead of the loopback we connect to.
- Preserves `Content-Encoding`: `aiter_raw()` yields the original transport
  bytes (still gzipped when upstream gzipped), so the encoding header has to
  ride along or the browser receives binary labelled as JSON.
- Strips `Content-Length` so Starlette can use chunked transfer for streaming.
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

# Headers clients must not be allowed to inject into the upstream webui request.
# The proxy re-injects these from trusted sources (request.url.* and the real Host
# header) below. Stripping here closes the header-injection / CSRF-bypass path.
_STRIP_FROM_CLIENT = frozenset(
    {
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "x-real-host",
    }
)


def _filter_request_headers(headers: "dict[str, str] | httpx.Headers") -> dict[str, str]:
    out: dict[str, str] = {}
    items = headers.items() if hasattr(headers, "items") else headers
    for key, value in items:
        lower = key.lower()
        if lower in _HOP_BY_HOP or lower == "host" or lower in _STRIP_FROM_CLIENT:
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


def _response_headers(upstream: httpx.Response) -> tuple[dict[str, str], list[str]]:
    # Returns (non-cookie headers as dict, set-cookie values as list).
    # Splitting keeps Set-Cookie out of the dict so duplicates aren't collapsed
    # — multiple Set-Cookie headers on one response are valid and the only
    # realistic multi-valued case here.
    headers: dict[str, str] = {}
    set_cookies: list[str] = []
    for key, value in upstream.headers.multi_items():
        lower = key.lower()
        if lower in _HOP_BY_HOP:
            continue
        if lower == "content-length":
            continue
        if lower == "set-cookie":
            set_cookies.append(value)
            continue
        headers[key] = value
    return headers, set_cookies


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

    # hermes-webui's CSRF check matches the browser's Origin against Host /
    # X-Forwarded-Host / X-Real-Host. httpx will set Host to the loopback
    # upstream, so without these the public hostname never appears in the
    # allowed-host set and every browser POST is rejected.
    original_host = request.headers.get("host")
    if original_host:
        headers.setdefault("x-forwarded-host", original_host)
        headers.setdefault("x-real-host", original_host)
    headers.setdefault("x-forwarded-proto", request.url.scheme)

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

    out_headers, set_cookies = _response_headers(upstream)
    response = StreamingResponse(
        _stream(), status_code=upstream.status_code, headers=out_headers
    )
    # MutableHeaders.append() preserves duplicates — multiple Set-Cookie
    # headers on one response are valid and dict assignment would collapse
    # them to the last value.
    for cookie in set_cookies:
        response.headers.append("set-cookie", cookie)
    return response
