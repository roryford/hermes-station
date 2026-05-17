"""HTMX dashboard page + status fragment tests.

The new `hermes_station.admin.htmx_dashboard` module exposes `dashboard_page`
(GET /admin) and `status_fragment` (GET /admin/_partial/status). The existing
stub `admin_index` in `routes.py` still owns `/admin` at the time these tests
were written, so we build a test app that prepends the new routes so they win
the lookup — once the routes are wired into `create_app()` proper, this helper
becomes redundant but stays correct.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from hermes_station.admin.htmx_dashboard import routes as htmx_routes
from hermes_station.admin.routes import admin_routes
from hermes_station.config import Paths, write_yaml_config


def _build_app() -> Starlette:
    """Build a Starlette app with htmx_dashboard routes taking precedence.

    Mirrors what `create_app()` will do once the wiring lands, minus the
    proxy + lifespan + static-files plumbing that the dashboard tests don't
    exercise.
    """
    base_routes: list[Route] = list(htmx_routes())
    # Skip the legacy stub `admin_index` (also matches GET /admin) so the new
    # dashboard wins. Everything else (login, logout, /admin/api/*) stays.
    base_routes.extend(
        route for route in admin_routes() if not (isinstance(route, Route) and route.path == "/admin")
    )
    app = Starlette(routes=base_routes)
    app.state.paths = Paths()
    app.state.webui = None
    app.state.gateway = None
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


@pytest.fixture
def client_app(fake_data_dir: Path, admin_password: str) -> Starlette:  # noqa: ARG001
    return _build_app()


async def test_dashboard_requires_admin(client_app: Starlette) -> None:
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/admin/login"


async def test_dashboard_renders_after_login(client_app: Starlette, admin_password: str) -> None:
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin")
        assert response.status_code == 200
        body = response.text
        assert "Dashboard" in body
        # The page shells out the panel via HTMX; the WebUI label lives in the fragment.
        # The page itself must at least carry the polling target.
        assert 'id="status-panel"' in body
        assert "/admin/_partial/status" in body


async def test_status_fragment_returns_html(client_app: Starlette, admin_password: str) -> None:
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/status")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        body = response.text
        assert "WebUI" in body
        assert "Gateway" in body


async def test_status_fragment_requires_admin(client_app: Starlette) -> None:
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/_partial/status", follow_redirects=False)
        # Fragment endpoint returns 401 (not a redirect) so HTMX doesn't swap a
        # login page into the dashboard panel.
        assert response.status_code == 401


async def test_dashboard_shows_provider_when_configured(
    client_app: Starlette, fake_data_dir: Path, admin_password: str
) -> None:
    write_yaml_config(
        fake_data_dir / ".hermes" / "config.yaml",
        {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}},
    )

    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        # Pull the fragment directly — the dashboard page itself is a shell;
        # the provider label is rendered in the fragment that HTMX loads.
        response = await client.get("/admin/_partial/status")
        assert response.status_code == 200
        body = response.text
        assert "Anthropic" in body, "PROVIDER_CATALOG['anthropic'] label should appear"
        assert "claude-sonnet-4.6" in body


# ---------------------------------------------------------------------------
# Pure badge helper unit tests (from test_coverage_boost.py)
# ---------------------------------------------------------------------------


def test_supervisor_badge_healthy() -> None:
    from hermes_station.admin.htmx_dashboard import _supervisor_badge

    badge = _supervisor_badge(running=True, healthy=True)
    assert badge["tone"] == "success"
    assert badge["label"] == "Healthy"


def test_supervisor_badge_starting() -> None:
    from hermes_station.admin.htmx_dashboard import _supervisor_badge

    badge = _supervisor_badge(running=True, healthy=False)
    assert badge["tone"] == "warning"
    assert badge["label"] == "Starting"


def test_supervisor_badge_stopped() -> None:
    from hermes_station.admin.htmx_dashboard import _supervisor_badge

    badge = _supervisor_badge(running=False, healthy=False)
    assert badge["tone"] == "muted"
    assert badge["label"] == "Stopped"


def test_gateway_badge_running() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=True, state="running")
    assert badge["tone"] == "success"


def test_gateway_badge_starting() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=True, state="starting")
    assert badge["tone"] == "warning"


def test_gateway_badge_startup_failed() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=False, state="startup_failed")
    assert badge["tone"] == "danger"


def test_gateway_badge_running_but_unknown_state() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=True, state="unknown")
    assert badge["tone"] == "warning"
    assert badge["label"] == "Starting"


def test_gateway_badge_stopped() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=False, state="stopped")
    assert badge["tone"] == "muted"


# ---------------------------------------------------------------------------
# _build_stages hint logic (lines 155, 157-160, 165)
# ---------------------------------------------------------------------------


def test_build_stages_hint_secured_missing_webui_password() -> None:
    """_hint_secured returns correct text when only webui password missing."""
    from hermes_station.admin.htmx_dashboard import _build_stages

    stages = _build_stages(
        webui_running=False,
        admin_password="adminpw",
        webui_password="",
        provider_configured=False,
        gateway_state="stopped",
    )
    secured_stage = next(s for s in stages if s["label"] == "Secured")
    assert not secured_stage["ok"]
    assert "HERMES_WEBUI_PASSWORD" in secured_stage["hint"]


def test_build_stages_hint_secured_missing_admin_password() -> None:
    """_hint_secured returns correct text when only admin password missing."""
    from hermes_station.admin.htmx_dashboard import _build_stages

    stages = _build_stages(
        webui_running=False,
        admin_password="",
        webui_password="webuipw",
        provider_configured=False,
        gateway_state="stopped",
    )
    secured_stage = next(s for s in stages if s["label"] == "Secured")
    assert "HERMES_ADMIN_PASSWORD" in secured_stage["hint"]


def test_build_stages_hint_secured_both_passwords_set() -> None:
    """_hint_secured returns empty string when both passwords are set (covers line 155)."""
    from hermes_station.admin.htmx_dashboard import _build_stages

    stages = _build_stages(
        webui_running=True,
        admin_password="adminpw",
        webui_password="webuipw",
        provider_configured=True,
        gateway_state="running",
    )
    secured_stage = next(s for s in stages if s["label"] == "Secured")
    assert secured_stage["ok"]
    assert secured_stage["hint"] == ""


def test_build_stages_hint_connected_no_provider() -> None:
    """_hint_connected returns provider-not-configured message (covers line 167)."""
    from hermes_station.admin.htmx_dashboard import _build_stages

    stages = _build_stages(
        webui_running=False,
        admin_password="adminpw",
        webui_password="webuipw",
        provider_configured=False,
        gateway_state="stopped",
    )
    connected_stage = next(s for s in stages if s["label"] == "Connected")
    assert not connected_stage["ok"]
    assert "Configure a provider" in connected_stage["hint"]


def test_build_stages_hint_connected_provider_but_not_running() -> None:
    """_hint_connected returns start-gateway message when provider is set (covers line 168)."""
    from hermes_station.admin.htmx_dashboard import _build_stages

    stages = _build_stages(
        webui_running=False,
        admin_password="adminpw",
        webui_password="webuipw",
        provider_configured=True,
        gateway_state="stopped",
    )
    connected_stage = next(s for s in stages if s["label"] == "Connected")
    assert not connected_stage["ok"]
    assert "Start the gateway" in connected_stage["hint"]


def test_build_stages_hint_connected_running_returns_empty() -> None:
    """_hint_connected returns empty string when gateway is running (covers line 165)."""
    from hermes_station.admin.htmx_dashboard import _build_stages

    stages = _build_stages(
        webui_running=True,
        admin_password="adminpw",
        webui_password="webuipw",
        provider_configured=True,
        gateway_state="running",
    )
    connected_stage = next(s for s in stages if s["label"] == "Connected")
    assert connected_stage["ok"]
    assert connected_stage["hint"] == ""


# ---------------------------------------------------------------------------
# _explain_setup branches (lines 200, 204-208, 219, 221, 223, 231, 240)
# ---------------------------------------------------------------------------


def _make_status(
    *,
    secured: bool = False,
    provider_configured: bool = False,
    gateway_state: str = "stopped",
    active_channels: list[str] | None = None,
    warnings: list[str] | None = None,
    versions: dict | None = None,
) -> dict:
    """Build a minimal status dict suitable for _explain_setup."""
    stages = [
        {"label": "Secured", "ok": secured},
        {"label": "Configured", "ok": provider_configured},
        {"label": "Connected", "ok": gateway_state == "running"},
        {"label": "Running", "ok": False},
        {"label": "Useful", "ok": False},
    ]
    channels = [{"label": ch, "enabled": True} for ch in (active_channels or [])]
    return {
        "stages": stages,
        "provider": {"configured": provider_configured, "label": "TestProvider", "id": "test"},
        "gateway": {"state": gateway_state},
        "channels": channels,
        "warnings": warnings or [],
        "versions": versions or {},
    }


def test_explain_setup_secured_branch() -> None:
    """Secured=True branch appends password-protected sentence (covers line 200)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    sentences = _explain_setup(_make_status(secured=True))
    assert any("password protected" in s for s in sentences)


def test_explain_setup_unsecured_with_password_warning() -> None:
    """Unsecured + password warning in warnings triggers unprotected sentence (covers lines 204-205)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    status = _make_status(
        secured=False,
        warnings=["Admin has no password — this control plane is unprotected. Set HERMES_ADMIN_PASSWORD."],
    )
    sentences = _explain_setup(status)
    assert any("unprotected" in s for s in sentences)


def test_explain_setup_gateway_running() -> None:
    """Gateway state=running appends running sentence (covers line 219)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    sentences = _explain_setup(_make_status(gateway_state="running"))
    assert any("running and connected" in s for s in sentences)


def test_explain_setup_gateway_starting() -> None:
    """Gateway state=starting appends transitional sentence (covers line 221)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    sentences = _explain_setup(_make_status(gateway_state="starting"))
    assert any("starting" in s for s in sentences)


def test_explain_setup_gateway_stopping() -> None:
    """Gateway state=stopping appends transitional sentence (covers line 221)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    sentences = _explain_setup(_make_status(gateway_state="stopping"))
    assert any("stopping" in s for s in sentences)


def test_explain_setup_gateway_startup_failed() -> None:
    """Gateway state=startup_failed appends failure sentence (covers line 223)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    sentences = _explain_setup(_make_status(gateway_state="startup_failed"))
    assert any("failed to start" in s for s in sentences)


def test_explain_setup_active_channels() -> None:
    """Active channels list appends channel names (covers line 231)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    sentences = _explain_setup(_make_status(active_channels=["Telegram", "Discord"]))
    assert any("Telegram" in s and "Discord" in s for s in sentences)


def test_explain_setup_no_persistent_volume_warning() -> None:
    """Volume warning in warnings list appends data-loss sentence (covers line 238)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    status = _make_status(warnings=["No persistent volume detected — /data appears..."])
    sentences = _explain_setup(status)
    assert any("persistent volume" in s for s in sentences)


def test_explain_setup_persistent_volume_attached() -> None:
    """No volume warning means volume-attached sentence (covers line 240)."""
    from hermes_station.admin.htmx_dashboard import _explain_setup

    sentences = _explain_setup(_make_status(warnings=[]))
    assert any("persistent /data volume is attached" in s for s in sentences)


# ---------------------------------------------------------------------------
# _build_guardrail_warnings (lines 254-272)
# ---------------------------------------------------------------------------


def test_build_guardrail_warnings_no_webui_password(tmp_path: Path) -> None:
    """No webui_password emits WebUI warning (covers lines 254-258)."""
    from hermes_station.admin.htmx_dashboard import _build_guardrail_warnings

    warnings = _build_guardrail_warnings(
        admin_password="adminpw",
        webui_password="",
        data_dir=tmp_path,
    )
    assert any("HERMES_WEBUI_PASSWORD" in w for w in warnings)


def test_build_guardrail_warnings_no_admin_password(tmp_path: Path) -> None:
    """No admin_password emits admin warning (covers lines 259-260)."""
    from hermes_station.admin.htmx_dashboard import _build_guardrail_warnings

    warnings = _build_guardrail_warnings(
        admin_password="",
        webui_password="webuipw",
        data_dir=tmp_path,
    )
    assert any("HERMES_ADMIN_PASSWORD" in w for w in warnings)


def test_build_guardrail_warnings_same_device_as_root(tmp_path: Path) -> None:
    """data_dir on same device as / emits volume warning (covers lines 264-269).

    In CI the tmp_path lives on the same filesystem as /, so this reliably
    exercises the data_dev == root_dev branch.
    """
    import os

    from hermes_station.admin.htmx_dashboard import _build_guardrail_warnings

    # Only run this branch test if tmp_path is actually on the same device
    try:
        data_dev = os.stat(str(tmp_path)).st_dev
        root_dev = os.stat("/").st_dev
    except OSError:
        pytest.skip("Cannot stat device numbers in this environment")

    warnings = _build_guardrail_warnings(
        admin_password="adminpw",
        webui_password="webuipw",
        data_dir=tmp_path,
    )
    if data_dev == root_dev:
        assert any("persistent volume" in w.lower() for w in warnings)
    else:
        # Different device — no volume warning, but no error either
        assert not any("persistent volume" in w.lower() for w in warnings)


def test_build_guardrail_warnings_different_device_no_volume_warning(tmp_path: Path) -> None:
    """data_dir on a different device than / produces no volume warning (covers 264->272 false branch)."""
    import os
    from unittest.mock import patch

    from hermes_station.admin.htmx_dashboard import _build_guardrail_warnings

    real_stat = os.stat

    def _mock_stat(path: str, **kwargs):  # type: ignore[override]
        result = real_stat(path, **kwargs)
        # Make the data_dir appear to be on device 9999 (different from /)
        if path == str(tmp_path):
            # Return a mock with a different st_dev
            class _FakeStat:
                st_dev = 9999

                def __getattr__(self, name: str):  # type: ignore[override]
                    return getattr(result, name)

            return _FakeStat()
        return result

    with patch("os.stat", side_effect=_mock_stat):
        warnings = _build_guardrail_warnings(
            admin_password="adminpw",
            webui_password="webuipw",
            data_dir=tmp_path,
        )
    assert not any("persistent volume" in w.lower() for w in warnings)


def test_build_guardrail_warnings_oserror_is_silent() -> None:
    """OSError from stat() is silently swallowed (covers lines 270-271)."""
    from pathlib import Path as _Path
    from unittest.mock import patch

    from hermes_station.admin.htmx_dashboard import _build_guardrail_warnings

    with patch("os.stat", side_effect=OSError("no such file")):
        warnings = _build_guardrail_warnings(
            admin_password="adminpw",
            webui_password="webuipw",
            data_dir=_Path("/nonexistent/path"),
        )
    # No volume warning — OSError was swallowed
    assert not any("persistent volume" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# memory_raw not-a-dict branch (line 94)
# ---------------------------------------------------------------------------


async def test_status_fragment_memory_non_dict(
    client_app: Starlette, fake_data_dir: Path, admin_password: str
) -> None:
    """Non-dict memory config is normalised to empty dict (covers line 94)."""
    write_yaml_config(
        fake_data_dir / ".hermes" / "config.yaml",
        {"memory": "not-a-dict"},
    )
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/status")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# explain_fragment endpoint (lines 342-345)
# ---------------------------------------------------------------------------


async def test_explain_fragment_returns_html(client_app: Starlette, admin_password: str) -> None:
    """Authenticated GET /admin/_partial/explain renders HTML (covers lines 342-345)."""
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.get("/admin/_partial/explain")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


async def test_explain_fragment_requires_auth(client_app: Starlette) -> None:
    """Unauthenticated GET /admin/_partial/explain returns 401 (covers lines 342-343)."""
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/_partial/explain")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# mcp_fragment_toggle (lines 316-338)
# ---------------------------------------------------------------------------


def _build_app_with_mcp(fake_data_dir: Path) -> Starlette:
    """Build test app with an MCP server pre-seeded in config.yaml."""
    import yaml

    config_path = fake_data_dir / ".hermes" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"mcp_servers": {"filesystem": {"command": "npx", "enabled": False}}}),
        encoding="utf-8",
    )

    base_routes: list[Route] = list(htmx_routes())
    base_routes.extend(
        route for route in admin_routes() if not (isinstance(route, Route) and route.path == "/admin")
    )
    app = Starlette(routes=base_routes)
    app.state.paths = Paths()
    app.state.webui = None
    app.state.gateway = None
    return app


async def test_mcp_toggle_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST to mcp toggle redirects to login (covers lines 316-318)."""
    app = _build_app_with_mcp(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/_partial/mcp/toggle",
            data={"name": "filesystem"},
            follow_redirects=False,
        )
    assert response.status_code == 302


async def test_mcp_toggle_success(fake_data_dir: Path, admin_password: str) -> None:
    """Authenticated MCP toggle enables server and returns success card (covers lines 323-329)."""
    app = _build_app_with_mcp(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/mcp/toggle",
            data={"name": "filesystem"},
        )
    assert response.status_code == 200
    body = response.text
    assert "filesystem" in body


async def test_mcp_toggle_unknown_name_returns_error(fake_data_dir: Path, admin_password: str) -> None:
    """Toggle with unknown server name returns error card (covers lines 330-332)."""
    app = _build_app_with_mcp(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/mcp/toggle",
            data={"name": "nonexistent-server"},
        )
    assert response.status_code == 200
    assert "error" in response.text.lower() or "Operation failed" in response.text


async def test_mcp_toggle_unexpected_exception_returns_error(
    fake_data_dir: Path, admin_password: str
) -> None:
    """Unexpected exception in toggle returns generic error card (covers lines 333-335)."""
    from unittest.mock import patch

    import hermes_station.admin.htmx_dashboard as _dash

    app = _build_app_with_mcp(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        with patch.object(_dash, "toggle_mcp_server", side_effect=RuntimeError("boom")):
            response = await client.post(
                "/admin/_partial/mcp/toggle",
                data={"name": "filesystem"},
            )
    assert response.status_code == 200
    assert "Operation failed" in response.text


async def test_mcp_toggle_with_gateway_restarts(fake_data_dir: Path, admin_password: str) -> None:
    """Toggle with a gateway present calls gateway.restart() (covers line 328)."""
    from unittest.mock import AsyncMock, MagicMock

    app = _build_app_with_mcp(fake_data_dir)
    mock_gateway = MagicMock()
    mock_gateway.restart = AsyncMock(return_value=None)
    app.state.gateway = mock_gateway

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        response = await client.post(
            "/admin/_partial/mcp/toggle",
            data={"name": "filesystem"},
        )
    assert response.status_code == 200
    mock_gateway.restart.assert_called_once()
