"""E2E tests for the webui session bridge against a running container.

Validates the `/admin/api/ping` dual-cookie endpoint end-to-end:

  - unauthenticated request returns 401
  - login via webui session yields ``via: webui_session``
  - logout invalidates the bridge

Skips automatically when ``HERMES_STATION_E2E_URL`` is unset so the default
host pytest run (no container) stays clean. Mirrors the pattern in
``tests/test_e2e_admin.py``.

Run with:

    HERMES_STATION_E2E_URL=http://localhost:8787 \
    HERMES_STATION_E2E_PASSWORD=test-admin-pw \
    uv run pytest tests/test_e2e_auth_bridge.py -v
"""

from __future__ import annotations

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("HERMES_STATION_E2E_URL")
    if not url:
        pytest.skip("HERMES_STATION_E2E_URL not set — requires a running container")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def webui_password() -> str:
    return os.environ.get("HERMES_STATION_E2E_PASSWORD", "test-admin-pw")


def test_ping_unauthenticated_returns_401(base_url: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        resp = client.get("/admin/api/ping")
    assert resp.status_code == 401, f"expected 401, got {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body.get("error") == "unauthorized"


def test_ping_with_webui_session_returns_via_webui(base_url: str, webui_password: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        login = client.post(
            "/api/auth/login",
            json={"password": webui_password},
        )
        assert login.status_code in (
            200,
            204,
        ), f"webui login failed: {login.status_code} {login.text[:200]}"
        assert "hermes_session" in client.cookies, "expected hermes_session cookie after webui login"

        ping = client.get("/admin/api/ping")
        assert ping.status_code == 200, f"ping failed: {ping.status_code} {ping.text[:200]}"
        body = ping.json()
        assert body.get("ok") is True
        assert body.get("via") == "webui_session"


def test_ping_after_logout_returns_401(base_url: str, webui_password: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        login = client.post(
            "/api/auth/login",
            json={"password": webui_password},
        )
        assert login.status_code in (200, 204)
        assert "hermes_session" in client.cookies

        logout = client.post("/api/auth/logout")
        assert logout.status_code in (200, 204, 302)

        ping = client.get("/admin/api/ping")
        assert ping.status_code == 401, (
            f"expected 401 after logout, got {ping.status_code}: {ping.text[:200]}"
        )
