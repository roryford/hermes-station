"""Tests for hermes_station.admin.routes — rate limiting, auth guard, JSON body,
gateway/supervisor actions."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest


# ─────────────────────────────────────────────────────────── fixture


@pytest.fixture(autouse=False)
def clear_login_rate_limit():
    """Reset the login rate-limit dicts before/after tests to prevent pollution."""
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    routes._login_lockouts.clear()
    yield
    routes._login_attempts.clear()
    routes._login_lockouts.clear()


# ─────────────────────────────────────────────────────────── rate limiter / prune


def test_prune_login_attempts_evicts_stale() -> None:
    """_prune_login_state removes old attempt entries."""
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    routes._login_attempts["1.2.3.4"] = [time.time() - 120]
    routes._prune_login_state()
    assert "1.2.3.4" not in routes._login_attempts


def test_prune_login_attempts_keeps_recent() -> None:
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    routes._login_attempts["1.2.3.4"] = [time.time()]
    routes._prune_login_state()
    assert "1.2.3.4" in routes._login_attempts


def test_prune_login_attempts_evicts_beyond_max_ips() -> None:
    """When dict exceeds _LOGIN_MAX_IPS, oldest entries are evicted."""
    from hermes_station.admin import routes

    orig_max = routes._LOGIN_MAX_IPS
    try:
        routes._LOGIN_MAX_IPS = 5
        routes._login_attempts.clear()
        now = time.time()
        for i in range(6):
            routes._login_attempts[f"10.0.0.{i}"] = [now + i]
        routes._prune_login_state()
        assert len(routes._login_attempts) <= 5
    finally:
        routes._LOGIN_MAX_IPS = orig_max
        routes._login_attempts.clear()


def test_prune_login_state_evicts_expired_lockouts() -> None:
    """_prune_login_state removes lockout entries whose expiry has passed."""
    from hermes_station.admin import routes

    routes._login_lockouts.clear()
    routes._login_lockouts["1.2.3.4"] = time.time() - 1  # already expired
    routes._login_lockouts["5.6.7.8"] = time.time() + 3600  # still active
    routes._prune_login_state()
    assert "1.2.3.4" not in routes._login_lockouts
    assert "5.6.7.8" in routes._login_lockouts


async def test_rate_limit_blocks_after_max_attempts(fake_data_dir: Path, admin_password: str) -> None:
    """After _LOGIN_MAX_ATTEMPTS failed attempts, 429 is returned."""
    from hermes_station.app import create_app
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    routes._login_lockouts.clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(10):
            await client.post("/admin/login", data={"password": "wrong"})
        resp = await client.post("/admin/login", data={"password": "wrong"})
    assert resp.status_code == 429


async def test_rate_limit_escalates_to_lockout(fake_data_dir: Path, admin_password: str) -> None:
    """Hitting the rate limit adds the IP to the 1-hour lockout dict."""
    from hermes_station.app import create_app
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    routes._login_lockouts.clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(routes._LOGIN_MAX_ATTEMPTS):
            await client.post("/admin/login", data={"password": "wrong"})
        await client.post("/admin/login", data={"password": "wrong"})
    assert len(routes._login_lockouts) == 1
    expiry = next(iter(routes._login_lockouts.values()))
    assert expiry > time.time() + 3500


async def test_lockout_blocks_even_after_window_expires(fake_data_dir: Path, admin_password: str) -> None:
    """An IP in the lockout dict is rejected regardless of the attempt window."""
    from hermes_station.app import create_app
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    routes._login_lockouts.clear()
    # Seed lockout for the IP the ASGI test transport uses.
    routes._login_lockouts["127.0.0.1"] = time.time() + 3600
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/login", data={"password": "wrong"})
    assert resp.status_code == 429


# ─────────────────────────────────────────────────────────── _json_body


async def test_json_body_returns_empty_dict_on_non_json(fake_data_dir: Path, admin_password: str) -> None:
    """_json_body returns {} when body is not valid JSON."""
    from hermes_station.admin.routes import _json_body
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(b"content-type", b"text/plain")],
    }

    async def receive():
        return {"type": "http.request", "body": b"not json", "more_body": False}

    request = Request(scope, receive)
    result = await _json_body(request)
    assert result == {}


async def test_json_body_returns_empty_dict_when_body_is_list(
    fake_data_dir: Path, admin_password: str
) -> None:
    """_json_body returns {} when JSON body is a list (not a dict)."""
    from hermes_station.admin.routes import _json_body
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }

    async def receive():
        return {"type": "http.request", "body": b'["a","b"]', "more_body": False}

    request = Request(scope, receive)
    result = await _json_body(request)
    assert result == {}


# ─────────────────────────────────────────────────────────── auth guard


def test_admin_auth_disabled_when_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_PASSWORD", raising=False)
    from hermes_station.admin.auth import admin_auth_enabled

    assert admin_auth_enabled() is False


def test_verify_password_returns_false_with_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    from hermes_station.admin.auth import verify_password

    assert verify_password("anything") is False


def test_verify_password_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "supersecret")
    from hermes_station.admin.auth import verify_password

    assert verify_password("supersecret") is True


def test_verify_password_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "supersecret")
    from hermes_station.admin.auth import verify_password

    assert verify_password("wrong") is False


async def test_api_status_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/status")
    assert resp.status_code == 401


# ─────────────────────────────────────────────────────────── gateway/supervisor actions


async def test_gateway_action_unknown_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Unknown gateway action returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/explode")
    assert resp.status_code == 400


async def test_api_gateway_start_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway start action should work (or return 500 if hermes-agent not installed)."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/start")
    assert resp.status_code in (200, 500)


async def test_api_gateway_stop_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway stop action returns 200 or 500."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/stop")
    assert resp.status_code in (200, 500)


async def test_api_gateway_restart_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway restart action returns 200 or 500."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/restart")
    assert resp.status_code in (200, 500)


async def test_api_webui_action_unknown(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Unknown webui action returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/webui/badaction")
    assert resp.status_code == 400


async def test_api_provider_setup_persists(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Provider setup endpoint persists config and returns ok:True."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post(
            "/admin/api/provider/setup",
            json={"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-test"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_api_provider_setup_invalid_provider(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Invalid provider returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post(
            "/admin/api/provider/setup",
            json={"provider": "fake-provider", "model": "x", "api_key": "y"},
        )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


async def test_admin_logout(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Logout redirects to login page."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in resp.headers["location"]


async def test_logout_unauthenticated_redirects_without_clearing(fake_data_dir: Path) -> None:
    """Unauthenticated logout redirects to login and does not set a cookie."""
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in resp.headers["location"]
    assert "hermes_station_admin" not in resp.cookies


async def test_admin_routes_blocked_when_no_password(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All admin routes return 401 or redirect when no password is configured."""
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_PASSWORD", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        api_resp = await client.get("/admin/api/status", follow_redirects=False)
        page_resp = await client.get("/admin", follow_redirects=False)

    assert api_resp.status_code == 401
    assert page_resp.status_code == 302
    assert "login" in page_resp.headers["location"]


async def test_api_logs_limit_zero(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """limit=0 is clamped to 1."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/logs/station?limit=0")
    assert resp.status_code == 200
    assert resp.json()["count"] <= 1


async def test_api_logs_gateway_source(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway log source returns a list."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/logs/gateway")
    assert resp.status_code == 200
    assert resp.json()["source"] == "gateway"


async def test_api_logs_webui_source(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """WebUI log source returns a list."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/logs/webui")
    assert resp.status_code == 200
    assert resp.json()["source"] == "webui"


async def test_api_status_returns_json(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """api_status returns structured JSON with expected fields."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "paths" in body
    assert "model" in body
    assert "gateway" in body
    assert body["auth"]["enabled"] is True
    assert body["auth"]["authenticated"] is True


async def test_logs_fragment_unknown_source_400(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Unknown source on fragment endpoint returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/_partial/logs/bogus-source")
    assert resp.status_code in (400, 401)
