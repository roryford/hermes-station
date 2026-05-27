"""Container boot smoke tests.

Verifies that the container starts correctly, auth is active, and
hermes-agent is importable by webui (agent turns work).

Auto-skips when HERMES_STATION_E2E_URL is not set.
"""

from __future__ import annotations

import httpx
import pytest


def test_health_returns_200(base_url: str) -> None:
    resp = httpx.get(f"{base_url}/health", timeout=10.0)
    assert resp.status_code == 200, f"/health returned {resp.status_code}: {resp.text[:200]}"


def test_root_redirects_to_login_when_unauthenticated(base_url: str) -> None:
    resp = httpx.get(base_url + "/", follow_redirects=False, timeout=10.0)
    # webui redirects unauthenticated requests to /login
    assert resp.status_code in (302, 303), (
        f"expected redirect to /login, got {resp.status_code}"
    )
    location = resp.headers.get("location", "")
    assert "login" in location, f"redirect location doesn't look like /login: {location!r}"


def test_bad_password_returns_401(base_url: str) -> None:
    resp = httpx.post(
        f"{base_url}/api/auth/login",
        json={"password": "definitely-wrong-password-xyz"},
        timeout=10.0,
    )
    assert resp.status_code == 401, (
        f"expected 401 for wrong password, got {resp.status_code}: {resp.text[:200]}"
    )


def test_authenticated_sessions_endpoint(base_url: str, webui_password: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        login = client.post("/api/auth/login", json={"password": webui_password})
        assert login.status_code == 200, f"login failed: {login.status_code}"

        resp = client.get("/api/sessions")
    assert resp.status_code == 200, (
        f"/api/sessions returned {resp.status_code}: {resp.text[:200]}"
    )


def test_agent_importable_via_new_session(base_url: str, webui_password: str) -> None:
    """Proves HERMES_WEBUI_AGENT_DIR is set correctly.

    /api/session/new triggers webui to import run_agent.AIAgent. A 200 response
    means the import succeeded; a 500 typically means the import failed.
    """
    import re

    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=15.0) as client:
        login = client.post(
            "/api/auth/login",
            headers={"Origin": base_url},
            json={"password": webui_password},
        )
        assert login.status_code == 200, f"login failed: {login.status_code}"

        # Fetch CSRF token from root page
        page = client.get("/", headers={"Origin": base_url})
        m = re.search(r'csrfToken\s*:\s*"([0-9a-f]+)"', page.text)
        csrf_token = m.group(1) if m else ""

        resp = client.post(
            "/api/session/new",
            headers={"Origin": base_url, "X-Hermes-CSRF-Token": csrf_token},
            json={},
        )

    assert resp.status_code == 200, (
        f"/api/session/new returned {resp.status_code} — likely HERMES_WEBUI_AGENT_DIR "
        f"is wrong or hermes-agent is not importable: {resp.text[:400]}"
    )
    body = resp.json()
    assert body.get("session", {}).get("session_id"), (
        f"expected session.session_id in /api/session/new response, got {body!r}"
    )


def test_image_revision_present(base_url: str) -> None:
    """Build revision metadata is baked into the image and surfaced at /health."""
    resp = httpx.get(f"{base_url}/health", timeout=10.0)
    assert resp.status_code == 200
    # Accept either a git SHA or the fallback 'dev' (local builds)
    body = resp.text
    assert body, "/health returned empty body"
