"""Unit tests for Phase 1 supervisor + proxy modules.

The actual subprocess / asyncio.Task lifecycle is exercised end-to-end via the
container smoke test (`scripts/smoke.sh` and `.github/workflows/ci.yml`). These
tests cover the pure-function pieces that don't need a real subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_station.gateway import Gateway, should_autostart
import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.proxy import _filter_request_headers, _strip_our_cookies, proxy_to_webui
from hermes_station.webui import WebUIProcess


# ───────────────────────────────────────────────────── gateway.should_autostart


def test_should_autostart_forced_on() -> None:
    assert should_autostart(mode="1", config={}, env_values={}) is True
    assert should_autostart(mode="true", config={}, env_values={}) is True
    assert should_autostart(mode="on", config={}, env_values={}) is True
    assert should_autostart(mode="YES", config={}, env_values={}) is True


def test_should_autostart_forced_off() -> None:
    assert should_autostart(mode="0", config={}, env_values={}) is False
    assert should_autostart(mode="false", config={}, env_values={}) is False
    assert should_autostart(mode="off", config={}, env_values={}) is False


def test_should_autostart_auto_requires_provider_and_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure no provider credentials leak in from other tests via os.environ
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # No provider configured
    assert (
        should_autostart(mode="auto", config={}, env_values={"TELEGRAM_BOT_TOKEN": "abc"})
        is False
    )
    # Provider set, but env var missing
    config = {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}}
    assert (
        should_autostart(mode="auto", config=config, env_values={"TELEGRAM_BOT_TOKEN": "abc"})
        is False
    )
    # Provider configured with key but no channel
    env = {"ANTHROPIC_API_KEY": "sk-ant-xxx"}
    assert should_autostart(mode="auto", config=config, env_values=env) is False
    # Provider configured AND channel configured
    env = {"ANTHROPIC_API_KEY": "sk-ant-xxx", "TELEGRAM_BOT_TOKEN": "12345:abc"}
    assert should_autostart(mode="auto", config=config, env_values=env) is True


def test_should_autostart_auto_picks_up_env_var_provider_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider key in os.environ counts, not just .env (CONTRACT.md §2.1 Option C)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-railway")
    config = {"model": {"provider": "anthropic"}}
    env = {"TELEGRAM_BOT_TOKEN": "12345:abc"}
    assert should_autostart(mode="auto", config=config, env_values=env) is True


def test_should_autostart_unknown_provider_returns_false() -> None:
    config = {"model": {"provider": "made-up-provider"}}
    assert should_autostart(mode="auto", config=config, env_values={"ANYTHING": "x"}) is False


def test_should_autostart_copilot_accepts_github_token_aliases() -> None:
    config = {"model": {"provider": "copilot", "default": "gpt-4.1"}}
    env = {"GITHUB_TOKEN": "gho-test-token", "TELEGRAM_BOT_TOKEN": "12345:abc"}
    assert should_autostart(mode="auto", config=config, env_values=env) is True


# ─────────────────────────────────────────────────────── proxy filters


def test_filter_request_headers_drops_hop_by_hop_and_host() -> None:
    headers = {
        "Host": "downstream.example",
        "Connection": "keep-alive",
        "Transfer-Encoding": "chunked",
        "Upgrade": "websocket",
        "X-Real-IP": "10.0.0.1",
        "User-Agent": "smoke",
        "Cookie": "a=1; b=2",
    }
    out = _filter_request_headers(headers)
    assert "Host" not in out
    assert "Connection" not in out
    assert "Transfer-Encoding" not in out
    assert "Upgrade" not in out
    assert out["X-Real-IP"] == "10.0.0.1"
    assert out["User-Agent"] == "smoke"
    assert out["Cookie"] == "a=1; b=2"


def test_strip_our_cookies_removes_only_admin_session() -> None:
    cookie = "hermes_station_admin=signed-blob; sessionid=webui-blob; theme=dark"
    out = _strip_our_cookies(cookie)
    assert "hermes_station_admin" not in out
    assert "sessionid=webui-blob" in out
    assert "theme=dark" in out


def test_strip_our_cookies_passes_through_other_cookies() -> None:
    cookie = "sessionid=webui-blob; csrf=xyz"
    out = _strip_our_cookies(cookie)
    assert out == "sessionid=webui-blob; csrf=xyz"


def test_strip_our_cookies_handles_empty() -> None:
    assert _strip_our_cookies(None) == ""
    assert _strip_our_cookies("") == ""


# ───────────────────────── proxy forwards Host so webui's CSRF check passes
# Regression: hermes-webui rejects browser POSTs when the request's Origin
# host doesn't match Host / X-Forwarded-Host / X-Real-Host. httpx overwrites
# Host with the loopback upstream, so the proxy must inject the forwarded
# headers — otherwise login + session creation silently 403.


async def test_proxy_forwards_host_and_scheme_for_csrf() -> None:
    captured: dict[str, httpx.Headers] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    class _FakeWebUI:
        INTERNAL_HOST = "127.0.0.1"
        INTERNAL_PORT = 8788

    async def _route(request):  # type: ignore[no-untyped-def]
        return await proxy_to_webui(request)

    app = Starlette(routes=[Route("/{path:path}", _route, methods=["POST"])])
    app.state.webui = _FakeWebUI()
    app.state.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://station.example.com"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            headers={"Origin": "https://station.example.com"},
            json={"password": "x"},
        )

    await app.state.proxy_client.aclose()

    assert resp.status_code == 200
    fwd = captured["headers"]
    assert fwd.get("x-forwarded-host") == "station.example.com"
    assert fwd.get("x-real-host") == "station.example.com"
    assert fwd.get("x-forwarded-proto") == "https"


# ───────────────── proxy preserves Content-Encoding so gzipped JSON works
# Regression: hermes-webui gzips responses >1KB. The proxy uses
# httpx.aiter_raw(), which yields the original compressed bytes, so the
# Content-Encoding header has to ride along — otherwise the browser sees
# gzipped bytes labelled as application/json and renders "Failed to load
# session" once a session payload crosses the gzip threshold.


async def test_proxy_preserves_content_encoding_for_gzip() -> None:
    import gzip

    payload = b'{"hello":"world"}' * 200  # ~3.5KB, gzips well
    gzipped = gzip.compress(payload)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
                "content-length": str(len(gzipped)),
            },
            stream=httpx.ByteStream(gzipped),
        )

    class _FakeWebUI:
        INTERNAL_HOST = "127.0.0.1"
        INTERNAL_PORT = 8788

    async def _route(request):  # type: ignore[no-untyped-def]
        return await proxy_to_webui(request)

    app = Starlette(routes=[Route("/{path:path}", _route, methods=["GET"])])
    app.state.webui = _FakeWebUI()
    app.state.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://station.test"
    ) as client:
        resp = await client.get("/api/session")

    await app.state.proxy_client.aclose()

    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    # httpx's client decodes Content-Encoding transparently, so by the time
    # resp.content is read it's already the decompressed payload. The header
    # surviving the proxy is the actual regression target.
    assert resp.content == payload


# ───── proxy preserves multiple Set-Cookie headers on a single response


async def test_proxy_preserves_multiple_set_cookie_headers() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[
                ("set-cookie", "a=1; Path=/"),
                ("set-cookie", "b=2; Path=/"),
                ("content-type", "text/plain"),
            ],
            stream=httpx.ByteStream(b"ok"),
        )

    class _FakeWebUI:
        INTERNAL_HOST = "127.0.0.1"
        INTERNAL_PORT = 8788

    async def _route(request):  # type: ignore[no-untyped-def]
        return await proxy_to_webui(request)

    app = Starlette(routes=[Route("/{path:path}", _route, methods=["GET"])])
    app.state.webui = _FakeWebUI()
    app.state.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://station.test"
    ) as client:
        resp = await client.get("/whatever")

    await app.state.proxy_client.aclose()

    cookies = resp.headers.get_list("set-cookie")
    assert "a=1; Path=/" in cookies
    assert "b=2; Path=/" in cookies


# ────────────────────────────────────────────────── supervisor state defaults


def test_webui_process_is_not_running_when_constructed(tmp_path: Path) -> None:
    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    assert proc.is_running() is False


def test_gateway_state_unknown_when_no_state_file(tmp_path: Path) -> None:
    gw = Gateway(hermes_home=tmp_path)
    assert gw.is_running() is False
    assert gw.gateway_state == "unknown"
    assert gw.is_healthy() is False


def test_gateway_state_reads_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "gateway_state.json"
    state_file.write_text('{"gateway_state": "running", "pid": 12345}')
    gw = Gateway(hermes_home=tmp_path)
    assert gw.gateway_state == "running"
    # is_healthy still False because task isn't set in this unit test
    assert gw.is_healthy() is False


def test_gateway_handles_malformed_state_file(tmp_path: Path) -> None:
    (tmp_path / "gateway_state.json").write_text("not json")
    gw = Gateway(hermes_home=tmp_path)
    assert gw.gateway_state == "unknown"
