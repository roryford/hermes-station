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
