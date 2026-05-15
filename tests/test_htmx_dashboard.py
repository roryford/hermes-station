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
