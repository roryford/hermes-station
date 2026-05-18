"""HTTP-level smoke tests against a running station container.

Two regression targets, both about proxy behavior in hermes_station/proxy.py:

1. PR #9 — `fix(proxy): forward Host so hermes-webui CSRF accepts browser
   POSTs`. hermes-webui's `_check_csrf` rejects POSTs whose Origin doesn't
   match Host / X-Forwarded-Host / X-Real-Host on the upstream side.
   Exercising that path only needs an explicit Origin header; a real
   browser is not required.

2. PR #11 — `fix(proxy): preserve Content-Encoding + Set-Cookie`. webui's
   `j()` helper gzips JSON responses >1KB. If the proxy strips
   Content-Encoding while forwarding raw bytes, clients receive gzipped
   bytes labelled application/json and fail to parse. httpx auto-decodes
   gzip and exposes the original Content-Encoding header, so it can
   verify both the encoding survived end-to-end and that the body parses.

Requires a running container. Auto-skips when env vars aren't set, so this
file is safe in the default pytest selection (the unit-test job skips it,
the build job in CI sets the env vars and runs it).
"""

from __future__ import annotations

import os
import re

import httpx
import pytest


def _fetch_csrf_token(client: httpx.Client, base_url: str) -> str:
    """GET the webui root page and extract the session-bound CSRF token.

    hermes-webui v0.51.88+ embeds the token in:
      window.__HERMES_CONFIG__={...,csrfToken:"<hex>",...}

    The token must be sent as ``X-Hermes-CSRF-Token`` on unsafe (POST) browser
    requests that carry an Origin header, otherwise webui rejects them with
    "Cross-origin request rejected".
    """
    page = client.get("/", headers={"Origin": base_url})
    m = re.search(r'csrfToken\s*:\s*"([0-9a-f]+)"', page.text)
    if not m:
        return ""
    return m.group(1)


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("HERMES_STATION_E2E_URL")
    if not url:
        pytest.skip("HERMES_STATION_E2E_URL not set — requires a running container")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def webui_password() -> str:
    pw = os.environ.get("HERMES_STATION_E2E_PASSWORD")
    if not pw:
        pytest.skip("HERMES_STATION_E2E_PASSWORD not set")
    return pw


def test_login_through_proxy_passes_csrf_and_sets_session_cookie(base_url: str, webui_password: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        resp = client.post(
            "/api/auth/login",
            headers={"Origin": base_url},
            json={"password": webui_password},
        )

    assert resp.status_code == 200, (
        f"login POST returned {resp.status_code} (body={resp.text!r}) — likely a "
        "CSRF reject; check that proxy.py forwards X-Forwarded-Host / X-Real-Host "
        "and that hermes-webui sees Origin matching Host."
    )
    assert "hermes_session" in resp.cookies, f"expected hermes_session cookie, got {dict(resp.cookies)}"


def test_gzipped_json_response_preserves_content_encoding(base_url: str, webui_password: str) -> None:
    """webui gzips JSON responses >1KB. /api/session/new returns ~1.5KB of
    metadata, so the proxy must keep Content-Encoding intact for the client
    to decode it. httpx auto-decompresses and still exposes the original
    Content-Encoding header on resp.headers.

    hermes-webui v0.51.88+ requires a session-bound CSRF token on any POST
    that carries an Origin header. We fetch the token from the root page
    (where webui embeds it in window.__HERMES_CONFIG__) and include it as
    X-Hermes-CSRF-Token — exactly what a browser's fetch interceptor does.
    """
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        login = client.post(
            "/api/auth/login",
            headers={"Origin": base_url},
            json={"password": webui_password},
        )
        assert login.status_code == 200, f"login failed: {login.status_code} {login.text!r}"

        # Fetch the CSRF token now that we have an authenticated session cookie.
        csrf_token = _fetch_csrf_token(client, base_url)
        assert csrf_token, "could not extract csrfToken from webui root page"

        resp = client.post(
            "/api/session/new",
            headers={"Origin": base_url, "X-Hermes-CSRF-Token": csrf_token},
            json={},
        )

    assert resp.status_code == 200, f"/api/session/new returned {resp.status_code} (body={resp.text[:200]!r})"
    assert resp.headers.get("content-encoding", "").lower() == "gzip", (
        f"proxy stripped Content-Encoding from a gzipped upstream response — headers={dict(resp.headers)}"
    )
    body = resp.json()
    assert body.get("session", {}).get("session_id"), f"expected session.session_id in response, got {body!r}"
