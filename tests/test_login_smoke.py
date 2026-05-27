"""HTTP-level smoke tests against a running container.

Auto-skips when HERMES_STATION_E2E_URL is not set, so this is safe in the
default pytest selection — the build job in CI sets the env var and runs it.
"""

from __future__ import annotations

import re

import httpx
import pytest


def _fetch_csrf_token(client: httpx.Client, base_url: str) -> str:
    """GET the webui root page and extract the session-bound CSRF token.

    hermes-webui embeds the token in:
      window.__HERMES_CONFIG__={...,csrfToken:"<hex>",...}

    The token must be sent as X-Hermes-CSRF-Token on unsafe (POST) requests
    that carry an Origin header.
    """
    page = client.get("/", headers={"Origin": base_url})
    m = re.search(r'csrfToken\s*:\s*"([0-9a-f]+)"', page.text)
    if not m:
        return ""
    return m.group(1)


def test_login_sets_session_cookie(base_url: str, webui_password: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        resp = client.post(
            "/api/auth/login",
            headers={"Origin": base_url},
            json={"password": webui_password},
        )

    assert resp.status_code == 200, (
        f"login POST returned {resp.status_code} (body={resp.text!r})"
    )
    assert "hermes_session" in resp.cookies, (
        f"expected hermes_session cookie, got {dict(resp.cookies)}"
    )


def test_gzipped_json_response_is_decodable(base_url: str, webui_password: str) -> None:
    """webui gzips JSON responses >1KB. Verify the body is decodable end-to-end."""
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        login = client.post(
            "/api/auth/login",
            headers={"Origin": base_url},
            json={"password": webui_password},
        )
        assert login.status_code == 200, f"login failed: {login.status_code} {login.text!r}"

        csrf_token = _fetch_csrf_token(client, base_url)
        assert csrf_token, "could not extract csrfToken from webui root page"

        resp = client.post(
            "/api/session/new",
            headers={"Origin": base_url, "X-Hermes-CSRF-Token": csrf_token},
            json={},
        )

    assert resp.status_code == 200, (
        f"/api/session/new returned {resp.status_code} (body={resp.text[:200]!r})"
    )
    body = resp.json()
    assert body.get("session", {}).get("session_id"), (
        f"expected session.session_id in response, got {body!r}"
    )
