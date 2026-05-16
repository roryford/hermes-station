"""Tests for hermes_station.admin.smoketest."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.admin.routes import admin_routes
from hermes_station.admin.smoketest import (
    _test_gateway,
    _test_github_mcp,
    _test_provider,
    _test_storage,
    _test_web_search,
    routes as smoketest_routes,
)
from hermes_station.config import Paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(gateway: Any = None) -> Starlette:
    app_routes: list[Route] = list(smoketest_routes())
    app_routes.extend(
        route
        for route in admin_routes()
        if not (isinstance(route, Route) and route.path == "/admin")
    )
    app = Starlette(routes=app_routes)
    app.state.paths = Paths()
    app.state.gateway = gateway
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post(
        "/admin/login", data={"password": password}, follow_redirects=False
    )
    assert response.status_code == 302, response.text


# ---------------------------------------------------------------------------
# Unit: _test_storage
# ---------------------------------------------------------------------------


async def test_storage_pass(tmp_path: Path) -> None:
    paths = MagicMock(spec=Paths)
    paths.home = str(tmp_path)
    result = await _test_storage(paths)
    assert result["status"] == "pass"
    assert result["name"] == "storage"
    assert str(tmp_path) in result["detail"]
    # Probe file should be cleaned up.
    assert not (tmp_path / ".smoketest_probe").exists()


async def test_storage_fail(tmp_path: Path) -> None:
    paths = MagicMock(spec=Paths)
    paths.home = "/nonexistent/path/that/cannot/be/written"
    result = await _test_storage(paths)
    assert result["status"] == "fail"
    assert "fix" in result
    assert result["fix"]


# ---------------------------------------------------------------------------
# Unit: _test_provider
# ---------------------------------------------------------------------------


async def test_provider_skip_no_provider() -> None:
    result = await _test_provider({}, {})
    assert result["status"] == "skip"
    assert result["name"] == "provider"


async def test_provider_fail_no_credential() -> None:
    config = {"model": {"provider": "openai"}}
    result = await _test_provider(config, {})
    assert result["status"] == "fail"
    assert "credential" in result["detail"].lower()


async def test_provider_pass_openai_200() -> None:
    config = {"model": {"provider": "openai"}}
    env = {"OPENAI_API_KEY": "sk-test"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_provider(config, env)

    assert result["status"] == "pass"
    assert "200" in result["detail"]


async def test_provider_fail_openai_401() -> None:
    config = {"model": {"provider": "openai"}}
    env = {"OPENAI_API_KEY": "sk-bad"}
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_provider(config, env)

    assert result["status"] == "fail"
    assert "401" in result["detail"]
    assert result["fix"]


async def test_provider_fail_timeout() -> None:
    config = {"model": {"provider": "anthropic"}}
    env = {"ANTHROPIC_API_KEY": "sk-ant-test"}

    async def fake_get(url: str, **kwargs: Any) -> None:
        raise httpx.TimeoutException("timed out")

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_provider(config, env)

    assert result["status"] == "fail"
    assert "timed out" in result["detail"].lower()


async def test_provider_copilot_skip_http_check() -> None:
    """Copilot skips the HTTP check but passes if credential is present."""
    config = {"model": {"provider": "copilot"}}
    env = {"COPILOT_GITHUB_TOKEN": "ghu_test"}
    result = await _test_provider(config, env)
    assert result["status"] == "pass"
    assert "HTTP check not available" in result["detail"]


async def test_provider_openrouter_pass() -> None:
    config = {"model": {"provider": "openrouter"}}
    env = {"OPENROUTER_API_KEY": "or-test"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_provider(config, env)

    assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# Unit: _test_gateway
# ---------------------------------------------------------------------------


async def test_gateway_skip_no_provider() -> None:
    result = await _test_gateway(None, {})
    assert result["status"] == "skip"
    assert result["name"] == "gateway"


async def test_gateway_fail_none_supervisor() -> None:
    config = {"model": {"provider": "openai"}}
    result = await _test_gateway(None, config)
    assert result["status"] == "fail"


async def test_gateway_pass_running() -> None:
    config = {"model": {"provider": "openai"}}
    gw = MagicMock()
    gw.gateway_state = "running"
    result = await _test_gateway(gw, config)
    assert result["status"] == "pass"


async def test_gateway_fail_stopped() -> None:
    config = {"model": {"provider": "openai"}}
    gw = MagicMock()
    gw.gateway_state = "stopped"
    result = await _test_gateway(gw, config)
    assert result["status"] == "fail"
    assert "stopped" in result["detail"]
    assert result["fix"]


# ---------------------------------------------------------------------------
# Unit: _test_github_mcp
# ---------------------------------------------------------------------------


async def test_github_mcp_skip_not_enabled() -> None:
    result = await _test_github_mcp({}, {})
    assert result["status"] == "skip"
    assert result["name"] == "github_mcp"


async def test_github_mcp_skip_disabled_explicitly() -> None:
    config = {"mcp_servers": {"github": {"enabled": False}}}
    result = await _test_github_mcp(config, {})
    assert result["status"] == "skip"


async def test_github_mcp_fail_no_token() -> None:
    config = {"mcp_servers": {"github": {"enabled": True}}}
    result = await _test_github_mcp(config, {})
    assert result["status"] == "fail"
    assert "GITHUB_TOKEN" in result["detail"]


async def test_github_mcp_pass_200() -> None:
    config = {"mcp_servers": {"github": {"enabled": True}}}
    env = {"GITHUB_TOKEN": "ghp_test"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"login": "testuser"}

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_github_mcp(config, env)

    assert result["status"] == "pass"
    assert "@testuser" in result["detail"]


async def test_github_mcp_fail_401() -> None:
    config = {"mcp_servers": {"github": {"enabled": True}}}
    env = {"GITHUB_TOKEN": "ghp_expired"}
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_github_mcp(config, env)

    assert result["status"] == "fail"
    assert "401" in result["detail"]
    assert result["fix"]


async def test_github_mcp_uses_gh_token_fallback() -> None:
    config = {"mcp_servers": {"github": {"enabled": True}}}
    env = {"GH_TOKEN": "ghp_fallback"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"login": "fallbackuser"}

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_github_mcp(config, env)

    assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# Unit: _test_web_search
# ---------------------------------------------------------------------------


async def test_web_search_skip_not_configured() -> None:
    result = await _test_web_search({}, {})
    assert result["status"] == "skip"
    assert result["name"] == "web_search"


async def test_web_search_fail_no_key() -> None:
    config = {"web": {"search_backend": "brave"}}
    result = await _test_web_search(config, {})
    assert result["status"] == "fail"
    assert "BRAVE_API_KEY" in result["detail"]


async def test_web_search_pass_brave_200() -> None:
    config = {"web": {"search_backend": "brave"}}
    env = {"BRAVE_API_KEY": "brave-key"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_web_search(config, env)

    assert result["status"] == "pass"


async def test_web_search_fail_brave_403() -> None:
    config = {"web": {"search_backend": "brave"}}
    env = {"BRAVE_API_KEY": "bad-key"}
    mock_resp = MagicMock()
    mock_resp.status_code = 403

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_web_search(config, env)

    assert result["status"] == "fail"
    assert "403" in result["detail"]
    assert result["fix"]


async def test_web_search_skip_unknown_backend() -> None:
    config = {"web": {"search_backend": "duckduckgo"}}
    env = {}
    result = await _test_web_search(config, env)
    assert result["status"] == "skip"
    assert "duckduckgo" in result["detail"]


# ---------------------------------------------------------------------------
# Integration: HTTP endpoints
# ---------------------------------------------------------------------------


async def test_smoketest_page_redirects_unauthenticated(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/smoketest", follow_redirects=False)
    assert response.status_code in (302, 303)


async def test_smoketest_page_renders_after_login(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/smoketest")
    assert response.status_code == 200
    assert "Smoke tests" in response.text
    assert "Run all tests" in response.text


async def test_smoketest_run_unauthenticated_returns_401(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/admin/_partial/smoketest/run")
    assert response.status_code == 401


async def test_smoketest_run_returns_results_html(
    fake_data_dir: Path, admin_password: str
) -> None:
    """Run endpoint returns a table with all five test rows."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post("/admin/_partial/smoketest/run")
    assert response.status_code == 200
    body = response.text
    # The results partial should contain the panel wrapper and test labels.
    assert "smoketest-panel" in body
    assert "Storage" in body
    assert "Provider" in body
    assert "Gateway" in body
    assert "GitHub MCP" in body
    assert "Web search" in body
    # Summary line should be present.
    assert "passed" in body
    assert "failed" in body
    assert "skipped" in body


async def test_smoketest_run_shows_run_again_button(
    fake_data_dir: Path, admin_password: str
) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post("/admin/_partial/smoketest/run")
    assert response.status_code == 200
    assert "Run again" in response.text
