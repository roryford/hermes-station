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
    _test_browser_backend,
    _test_gateway,
    _test_github_mcp,
    _test_image_gen,
    _test_mcp_urls,
    _test_plugin_registry,
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
        route for route in admin_routes() if not (isinstance(route, Route) and route.path == "/admin")
    )
    app = Starlette(routes=app_routes)
    app.state.paths = Paths()
    app.state.gateway = gateway
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
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


async def test_gateway_fail_attribute_error() -> None:
    config = {"model": {"provider": "openai"}}
    gw = MagicMock()
    type(gw).gateway_state = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    result = await _test_gateway(gw, config)
    assert result["status"] == "fail"
    assert "boom" in result["detail"]


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


async def test_web_search_fail_unknown_backend() -> None:
    config = {"web": {"search_backend": "duckduckgo"}}
    result = await _test_web_search(config, {})
    assert result["status"] == "fail"
    assert "duckduckgo" in result["detail"]
    assert result["fix"]


async def test_web_search_pass_tavily_200() -> None:
    config = {"web": {"search_backend": "tavily"}}
    env = {"TAVILY_API_KEY": "tvly-test"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_post(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_web_search(config, env)

    assert result["status"] == "pass"
    assert "Tavily" in result["detail"]


async def test_web_search_fail_tavily_401() -> None:
    config = {"web": {"search_backend": "tavily"}}
    env = {"TAVILY_API_KEY": "bad-key"}
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    async def fake_post(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_web_search(config, env)

    assert result["status"] == "fail"
    assert "401" in result["detail"]
    assert result["fix"]


async def test_web_search_fail_tavily_no_key() -> None:
    config = {"web": {"search_backend": "tavily"}}
    result = await _test_web_search(config, {})
    assert result["status"] == "fail"
    assert "TAVILY_API_KEY" in result["detail"]


async def test_web_search_pass_ddgs_no_key_needed() -> None:
    config = {"web": {"search_backend": "ddgs"}}
    result = await _test_web_search(config, {})
    assert result["status"] == "pass"
    assert "no api key" in result["detail"].lower()


async def test_web_search_pass_key_present_firecrawl() -> None:
    config = {"web": {"search_backend": "firecrawl"}}
    result = await _test_web_search(config, {"FIRECRAWL_API_KEY": "fc-x"})
    assert result["status"] == "pass"
    assert "FIRECRAWL_API_KEY" in result["detail"]


async def test_web_search_fail_key_missing_firecrawl() -> None:
    config = {"web": {"search_backend": "firecrawl"}}
    result = await _test_web_search(config, {})
    assert result["status"] == "fail"
    assert "FIRECRAWL_API_KEY" in result["detail"]


# ---------------------------------------------------------------------------
# Unit: _test_image_gen
# ---------------------------------------------------------------------------


async def test_image_gen_skip_not_intended() -> None:
    result = await _test_image_gen({}, {})
    assert result["status"] == "skip"
    assert result["name"] == "image_gen"
    assert "not configured" in result["detail"].lower()


async def test_image_gen_fail_no_key() -> None:
    config = {"toolsets": ["image_gen"]}
    with patch("hermes_station.admin.smoketest.os.environ", {}):
        result = await _test_image_gen(config, {})
    assert result["status"] == "fail"
    assert "FAL_KEY" in result["detail"]
    assert result["fix"]


async def test_image_gen_pass_200() -> None:
    config = {"toolsets": ["image_gen"]}
    env = {"FAL_KEY": "fal-test-key"}
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
        result = await _test_image_gen(config, env)

    assert result["status"] == "pass"
    assert "fal.ai" in result["detail"]


async def test_image_gen_fail_403() -> None:
    config = {"toolsets": ["image_gen"]}
    env = {"FAL_KEY": "fal-bad-key"}
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
        result = await _test_image_gen(config, env)

    assert result["status"] == "fail"
    assert "403" in result["detail"]
    assert result["fix"]


async def test_image_gen_fail_timeout() -> None:
    config = {"toolsets": ["image_gen"]}
    env = {"FAL_KEY": "fal-test-key"}

    async def fake_get(url: str, **kwargs: Any) -> None:
        raise httpx.TimeoutException("timed out")

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_image_gen(config, env)

    assert result["status"] == "fail"
    assert "timed out" in result["detail"].lower()


# ---------------------------------------------------------------------------
# Integration: HTTP endpoints
# ---------------------------------------------------------------------------


async def test_smoketest_page_redirects_unauthenticated(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/smoketest", follow_redirects=False)
    assert response.status_code in (302, 303)


async def test_smoketest_page_renders_after_login(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/smoketest")
    assert response.status_code == 200
    assert "Smoke tests" in response.text
    assert "Run all tests" in response.text


async def test_smoketest_run_unauthenticated_returns_401(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/admin/_partial/smoketest/run")
    assert response.status_code == 401


async def test_smoketest_run_returns_results_html(fake_data_dir: Path, admin_password: str) -> None:
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


async def test_smoketest_run_shows_run_again_button(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post("/admin/_partial/smoketest/run")
    assert response.status_code == 200
    assert "Run again" in response.text


# ---------------------------------------------------------------------------
# Unit: _test_browser_backend
# ---------------------------------------------------------------------------


async def test_browser_backend_skip_none_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CAMOFOX_URL",
        "BROWSERBASE_API_KEY",
        "BROWSERBASE_PROJECT_ID",
        "BROWSER_USE_API_KEY",
        "STEEL_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    result = await _test_browser_backend({})
    assert result["status"] == "skip"
    assert result["name"] == "browser_backend"


async def test_browser_backend_camofox_pass_200() -> None:
    env = {"CAMOFOX_URL": "http://camofox.internal:9377"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_browser_backend(env)

    assert result["status"] == "pass"
    assert "camofox.internal" in result["detail"].lower() or "Camofox" in result["detail"]


async def test_browser_backend_camofox_connection_error() -> None:
    env = {"CAMOFOX_URL": "http://camofox.internal:9377"}

    async def fake_get(url: str, **kwargs: Any) -> None:
        raise httpx.ConnectError("connection refused")

    with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        result = await _test_browser_backend(env)

    assert result["status"] == "fail"
    assert result["fix"]


async def test_browser_backend_browserbase_pass_200() -> None:
    env = {"BROWSERBASE_API_KEY": "bb-key", "BROWSERBASE_PROJECT_ID": "proj-123"}
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
        result = await _test_browser_backend(env)

    assert result["status"] == "pass"


async def test_browser_backend_browserbase_fail_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAMOFOX_URL", raising=False)
    env = {"BROWSERBASE_API_KEY": "bad-key", "BROWSERBASE_PROJECT_ID": "proj-123"}
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
        result = await _test_browser_backend(env)

    assert result["status"] == "fail"
    assert "invalid" in result["detail"].lower()
    assert result["fix"]


async def test_browser_backend_browser_use_credential_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAMOFOX_URL", raising=False)
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    env = {"BROWSER_USE_API_KEY": "bu-secret"}
    result = await _test_browser_backend(env)
    assert result["status"] == "pass"
    assert "Browser Use credential is present" in result["detail"]


async def test_browser_backend_steel_credential_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAMOFOX_URL", raising=False)
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    env = {"STEEL_API_KEY": "steel-secret"}
    result = await _test_browser_backend(env)
    assert result["status"] == "pass"
    assert "Steel credential is present" in result["detail"]


# ---------------------------------------------------------------------------
# Unit: _test_plugin_registry
# ---------------------------------------------------------------------------


async def test_plugin_registry_skip_no_dir(tmp_path: Path) -> None:
    """When the plugins root doesn't exist the test should be skipped."""
    fake_purelib = tmp_path / "site-packages"
    fake_purelib.mkdir()
    import sysconfig as _sysconfig

    with patch.object(_sysconfig, "get_paths", return_value={"purelib": str(fake_purelib)}):
        result = await _test_plugin_registry()
    assert result["name"] == "plugin_registry"
    assert result["status"] == "skip"
    assert "not found" in result["detail"].lower()


async def test_plugin_registry_pass_with_manifests(tmp_path: Path) -> None:
    """When manifests are present the test should pass and report the count."""
    fake_purelib = tmp_path / "site-packages"
    (fake_purelib / "plugins" / "web" / "plugin_a").mkdir(parents=True)
    (fake_purelib / "plugins" / "web" / "plugin_b").mkdir(parents=True)
    (fake_purelib / "plugins" / "web" / "plugin_a" / "plugin.yaml").write_text("name: a")
    (fake_purelib / "plugins" / "web" / "plugin_b" / "plugin.yaml").write_text("name: b")
    import sysconfig as _sysconfig

    with patch.object(_sysconfig, "get_paths", return_value={"purelib": str(fake_purelib)}):
        result = await _test_plugin_registry()
    assert result["status"] == "pass"
    assert "2" in result["detail"]
    assert result["name"] == "plugin_registry"


async def test_plugin_registry_fail_dir_exists_no_yamls(tmp_path: Path) -> None:
    """When the plugins/web dir exists but has no yaml files the test should fail."""
    fake_purelib = tmp_path / "site-packages"
    (fake_purelib / "plugins" / "web" / "plugin_a").mkdir(parents=True)
    # No plugin.yaml files — just an empty subdirectory.
    import sysconfig as _sysconfig

    with patch.object(_sysconfig, "get_paths", return_value={"purelib": str(fake_purelib)}):
        result = await _test_plugin_registry()
    assert result["status"] == "fail"
    assert result["fix"]
    assert "#27240" in result["fix"] or "#27268" in result["fix"]


async def test_plugin_registry_detail_contains_count(tmp_path: Path) -> None:
    """Detail string must contain the manifest count."""
    fake_purelib = tmp_path / "site-packages"
    for i in range(7):
        (fake_purelib / "plugins" / "web" / f"plugin_{i}").mkdir(parents=True)
        (fake_purelib / "plugins" / "web" / f"plugin_{i}" / "plugin.yaml").write_text(f"name: p{i}")
    import sysconfig as _sysconfig

    with patch.object(_sysconfig, "get_paths", return_value={"purelib": str(fake_purelib)}):
        result = await _test_plugin_registry()
    assert result["status"] == "pass"
    assert "7" in result["detail"]


# ---------------------------------------------------------------------------
# Unit: _test_mcp_urls
# ---------------------------------------------------------------------------


def _url_row(
    name: str = "playwright-mcp",
    url: str = "http://playwright-mcp.railway.internal:8931/mcp",
    enabled: bool = True,
) -> dict[str, Any]:
    return {"name": name, "url": url, "enabled": enabled, "is_url_based": True}


async def test_mcp_urls_skip_no_url_based_servers() -> None:
    """No URL-based servers in config → skip."""
    with patch("hermes_station.admin.mcp.mcp_status", return_value=[]):
        result = await _test_mcp_urls({}, {})
    assert result["status"] == "skip"
    assert result["name"] == "mcp_urls"
    assert "No URL-based" in result["detail"]


async def test_mcp_urls_skip_url_based_but_not_enabled() -> None:
    """URL-based server present but disabled → skip."""
    disabled_row = _url_row(enabled=False)
    with patch("hermes_station.admin.mcp.mcp_status", return_value=[disabled_row]):
        result = await _test_mcp_urls({}, {})
    assert result["status"] == "skip"


async def test_mcp_urls_pass_one_reachable_200() -> None:
    """One enabled URL-based server responds 200 → pass."""
    row = _url_row()
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return mock_resp

    with patch("hermes_station.admin.mcp.mcp_status", return_value=[row]):
        with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client
            result = await _test_mcp_urls({}, {})

    assert result["status"] == "pass"
    assert "playwright-mcp" in result["detail"]
    assert result["fix"] == ""


async def test_mcp_urls_fail_connection_error() -> None:
    """One enabled URL-based server raises ConnectError → fail with server name."""
    row = _url_row()

    async def fake_get(url: str, **kwargs: Any) -> None:
        raise httpx.ConnectError("connection refused")

    with patch("hermes_station.admin.mcp.mcp_status", return_value=[row]):
        with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client
            result = await _test_mcp_urls({}, {})

    assert result["status"] == "fail"
    assert "playwright-mcp" in result["detail"]
    assert result["fix"]


async def test_mcp_urls_fail_mixed_reachable_and_unreachable() -> None:
    """Two servers: one reachable, one not → fail listing both unreachable names."""
    row_ok = _url_row(name="mcp-a", url="http://mcp-a.internal/mcp")
    row_bad = _url_row(name="mcp-b", url="http://mcp-b.internal/mcp")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        if "mcp-a" in url:
            return mock_resp
        raise httpx.ConnectError("refused")

    with patch("hermes_station.admin.mcp.mcp_status", return_value=[row_ok, row_bad]):
        with patch("hermes_station.admin.smoketest.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client
            result = await _test_mcp_urls({}, {})

    assert result["status"] == "fail"
    assert "mcp-b" in result["detail"]
    assert result["fix"]
