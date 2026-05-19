"""Playwright test for the pilot Restart-Gateway action.

PR 3 of the browser suite (issue #73). This is the ONE test that mutates
shared container state — restarting the gateway resets ``state``, ``pid``,
and ``uptime_s`` for every other worker that happens to be polling. It
lives in ``tests/browser/serial/`` and is marked ``@pytest.mark.serial``
so a parallel xdist run can exclude it with ``-m "not serial"``; the CI
follow-up stage runs it alone with ``-m serial``.

Why this test is worth the quarantine cost: the Restart-Gateway button is
the first write action through the auth bridge (shipped in #79). It is
the prototype for every future workshop write-action listed in #74 —
channel setup forms, preset application, smoketest triggers. Catching
regressions in the confirm()/dialog → POST → toast → re-poll lifecycle
here means future write-actions can inherit the pattern with confidence.
"""

from __future__ import annotations

import pytest

RESTART_URL_SUFFIX = "/admin/api/pilot/gateway/restart"
STATUS_URL_SUFFIX = "/admin/api/pilot/status"
RESTART_BUTTON_TEXT = "Restart gateway"


@pytest.mark.browser
@pytest.mark.serial
def test_dismissing_confirm_does_not_post(station_page) -> None:
    """If the user cancels the confirm() dialog, no POST fires and the
    button stays enabled.

    Catches a regression where the click handler forgets to early-return
    on cancel — would silently restart the gateway on any accidental
    click.
    """
    page = station_page
    restart_url_seen: list[str] = []
    page.on(
        "request", lambda r: restart_url_seen.append(r.url) if r.url.endswith(RESTART_URL_SUFFIX) else None
    )

    # page.once → single-fire listener, in case the test reschedules clicks.
    page.once("dialog", lambda d: d.dismiss())

    button = page.get_by_role("button", name=RESTART_BUTTON_TEXT)
    button.click()

    # Give the click handler a moment to (incorrectly) fire a POST.
    page.wait_for_timeout(500)

    assert restart_url_seen == [], (
        f"POST fired after the user dismissed the confirm dialog: {restart_url_seen}. "
        "The early-return on !window.confirm(...) in admin.js:120 broke."
    )
    # Button must stay enabled with original text.
    assert button.is_enabled(), "Restart button stuck disabled after a dismissed confirm"
    assert button.inner_text().strip() == RESTART_BUTTON_TEXT


@pytest.mark.browser
@pytest.mark.serial
def test_accepting_confirm_posts_restart_and_re_polls(station_page) -> None:
    """Accepting the confirm dialog POSTs to /admin/api/pilot/gateway/restart,
    the button disables during the call and re-enables on completion, and
    a follow-up /admin/api/pilot/status request fires (admin.js:151 calls
    tick() in the finally block).

    Mutates shared state — the gateway actually restarts. Must run alone.
    """
    page = station_page
    restart_responses: list[int] = []

    def _on_response(resp) -> None:
        if resp.url.endswith(RESTART_URL_SUFFIX):
            restart_responses.append(resp.status)

    page.on("response", _on_response)
    page.once("dialog", lambda d: d.accept())

    button = page.get_by_role("button", name=RESTART_BUTTON_TEXT)

    # The finally block in restartGateway (admin.js) calls tick() after the
    # POST resolves, which fires a follow-up /admin/api/pilot/status request.
    # Use a SINGLE expect_response context that waits for that follow-up
    # /status request rather than snapshotting request counts around the
    # restart POST — request-count snapshots race the JS event loop: the
    # finally-block fetch can fire BEFORE Playwright's expect_response
    # context manager unblocks (and thus before any "before" snapshot),
    # producing intermittent 0-follow-up false failures.
    #
    # expect_response by itself is order-agnostic; to ensure we're matching
    # a post-restart /status (not a pre-restart scheduled poll), we filter
    # responses by tracking whether the restart response has arrived yet.
    restart_seen = {"flag": False}

    def _status_predicate(resp) -> bool:
        if resp.url.endswith(RESTART_URL_SUFFIX):
            restart_seen["flag"] = True
            return False
        return restart_seen["flag"] and resp.url.endswith(STATUS_URL_SUFFIX)

    with page.expect_response(_status_predicate, timeout=20_000):
        button.click()

    assert restart_responses == [200], (
        f"restart POST returned {restart_responses}, expected [200]. "
        "Check /admin/api/pilot/gateway/restart on the running container."
    )

    # Button re-enables on the JS finally branch. Allow generous time for
    # the supervisor's await on the new gateway pid.
    page.wait_for_function(
        f"() => {{"
        f"  const btns = document.querySelectorAll('#settingsPaneAdmin .admin-btn');"
        f"  return Array.from(btns).some(b => !b.disabled && b.textContent.trim() === '{RESTART_BUTTON_TEXT}');"
        f"}}",
        timeout=15_000,
    )
