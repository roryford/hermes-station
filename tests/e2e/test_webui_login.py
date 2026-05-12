"""End-to-end: a real browser logs into the WebUI through the station proxy.

Regression target — `fix(proxy): forward Host so hermes-webui CSRF accepts
browser POSTs` (PR #9). The CSRF check rejects POSTs whose Origin host
doesn't match Host / X-Forwarded-Host / X-Real-Host on the upstream
request; only a real browser sends Origin, so this can't be exercised
with httpx or curl. If the proxy ever stops forwarding Host again, this
test fails at the login step.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_login_succeeds_and_sets_session_cookie(
    page: Page, base_url: str, webui_password: str
) -> None:
    page.goto(f"{base_url}/login")
    expect(page).to_have_url(re.compile(r".*/login.*"))

    page.locator("#pw").fill(webui_password)
    with page.expect_response(re.compile(r".*/api/auth/login$")) as resp_info:
        page.locator("#login-form button[type=submit]").click()

    response = resp_info.value
    assert response.status == 200, (
        f"login POST returned {response.status} — likely CSRF reject; check that "
        "proxy.py forwards X-Forwarded-Host / X-Real-Host"
    )

    expect(page).not_to_have_url(re.compile(r".*/login.*"), timeout=10_000)

    cookies = page.context.cookies(base_url)
    names = {c["name"] for c in cookies}
    assert "hermes_session" in names, f"expected hermes_session cookie, got {names}"


def test_gzipped_json_api_parses_in_browser(
    page: Page, base_url: str, webui_password: str
) -> None:
    """hermes-webui gzips JSON responses >1KB. The proxy must keep
    Content-Encoding intact or the browser receives binary labelled as JSON
    and surfaces 'Failed to load session'. /api/session/new returns ~1.5KB
    of metadata — large enough to trigger gzip in `j()`."""
    page.goto(f"{base_url}/login")
    page.locator("#pw").fill(webui_password)
    with page.expect_response(re.compile(r".*/api/auth/login$")):
        page.locator("#login-form button[type=submit]").click()
    expect(page).not_to_have_url(re.compile(r".*/login.*"), timeout=10_000)

    result = page.evaluate(
        """async () => {
            const res = await fetch('/api/session/new', {
                method: 'POST',
                credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: '{}',
            });
            const encoding = res.headers.get('content-encoding');
            let parsed = null;
            let parseError = null;
            try { parsed = await res.json(); }
            catch (e) { parseError = String(e); }
            return {
                status: res.status,
                encoding,
                hasSessionId: !!(parsed && parsed.session && parsed.session.session_id),
                parseError,
            };
        }"""
    )
    assert result["status"] == 200, result
    assert result["parseError"] is None, (
        f"browser couldn't parse the response — proxy likely stripped "
        f"Content-Encoding from a gzipped body: {result}"
    )
    assert result["hasSessionId"], result
