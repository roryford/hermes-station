"""Playwright tests for the pilot admin extension's polling lifecycle.

PR 2 of the browser suite (issue #73). Three regression targets, all of
which httpx cannot see because they're about JS runtime behavior:

  3. Polling stops when the station pane loses .active. extension/admin.js
     wires a MutationObserver (admin.js:206) and a visibilitychange
     listener (admin.js:207) that call stop() when isActive() flips false.
     A leak here means the extension hammers /admin/api/pilot/status
     forever after the user navigates away from Settings → Station.

  4. Long-poll soak: with the pane open continuously, polls fire at the
     declared ~5s cadence with no console errors, no page errors, and no
     accumulating timer drift. Default duration is short enough to run in
     CI; operators can bump it via HERMES_STATION_BROWSER_SOAK_SECONDS to
     satisfy the #73 "10+ minutes" manual checkbox.

  5. /api/auth/status shape contract from the JS runtime. The extension
     relies on webui's session endpoint exposing ``auth_enabled`` and
     ``logged_in``; an upstream rename would silently break the bridge. We
     fetch it from the page context (not httpx) so any browser-only
     wrapping shows up.
"""

from __future__ import annotations

import os
import time

import pytest

POLL_URL_SUFFIX = "/admin/api/pilot/status"
POLL_MS = 5_000  # mirrors admin.js POLL_MS
SOAK_SECONDS_DEFAULT = 20  # captures ~3-4 polls
SOAK_SECONDS_ENV = "HERMES_STATION_BROWSER_SOAK_SECONDS"


def _is_status_request(url: str) -> bool:
    return url.endswith(POLL_URL_SUFFIX)


@pytest.mark.browser
def test_polling_stops_when_pane_loses_active(station_page) -> None:
    """After switching away from Station, no further /admin/api/pilot/status
    requests fire.

    Catches MutationObserver / visibilitychange wiring regressions in
    extension/admin.js:177-208. A leak here would flood the bridge and
    surface as a slow-creep load problem on long-lived sessions.
    """
    page = station_page
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url) if _is_status_request(r.url) else None)

    # Phase 1: pane is active — let it poll a few times so we know polling
    # is genuinely running before we test the stop path.
    page.wait_for_timeout(int(POLL_MS * 2.2))  # ~11s → 2 polls + the initial
    polls_while_active = len(requests)
    assert polls_while_active >= 2, (
        f"expected ≥2 status polls while pane was active, saw {polls_while_active}. "
        "Either the initial render didn't tick or the 5s schedule misfired."
    )

    # Phase 2: switch away. Our switchSettingsSection wrap should clear
    # .active on the pane, the MutationObserver should fire, and stop()
    # should clear the timer.
    page.evaluate("() => window.switchSettingsSection('conversation')")

    # Grace window: any request already in flight when we switched away
    # will still land. POLL_MS is the worst case for a scheduled tick that
    # was about to fire.
    page.wait_for_timeout(POLL_MS + 1_000)

    requests.clear()
    # Phase 3: with the pane inactive, watch for ~3 missed-poll windows.
    # If polling leaked we'd see 2-3 requests here.
    page.wait_for_timeout(int(POLL_MS * 3.2))  # ~16s

    assert requests == [], (
        f"polling did not stop after pane lost .active — saw {len(requests)} "
        f"leaked requests in the {POLL_MS * 3 // 1000}s window. URLs: {requests}"
    )


@pytest.mark.browser
def test_long_poll_soak_clean_and_steady(station_page) -> None:
    """With Station open continuously, polls fire on cadence, the console
    stays clean, and no unhandled errors surface.

    Default 20s; bump via ``HERMES_STATION_BROWSER_SOAK_SECONDS`` for the
    #73 long-soak manual run (10+ min). The cadence check uses generous
    tolerance — the JS spec only promises "at least" the delay, and slow
    CI hosts routinely miss tight windows.
    """
    page = station_page
    soak_seconds = int(os.environ.get(SOAK_SECONDS_ENV, SOAK_SECONDS_DEFAULT))

    request_times: list[float] = []
    console_errors: list[str] = []
    page_errors: list[str] = []

    page.on("request", lambda r: request_times.append(time.monotonic()) if _is_status_request(r.url) else None)
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))

    # Already on the station pane (fixture clicked through). Wait the soak
    # window. Use Playwright's wait_for_timeout rather than time.sleep so
    # the page's event loop stays serviced.
    page.wait_for_timeout(soak_seconds * 1_000)

    assert console_errors == [], f"console errors during soak: {console_errors}"
    assert page_errors == [], f"page errors during soak: {page_errors}"

    # Expected polls: initial + one per POLL_MS interval. Allow ±1 in
    # either direction for boundary timing.
    expected_min = max(1, soak_seconds * 1_000 // POLL_MS - 1)
    assert len(request_times) >= expected_min, (
        f"saw {len(request_times)} polls in {soak_seconds}s, expected ≥{expected_min}. "
        "Polling may have stalled mid-soak."
    )

    # Cadence: gaps between consecutive polls should cluster near POLL_MS.
    # Tolerate up to 2× POLL_MS on any single gap (one missed schedule is
    # plausible on a loaded CI host); the median, however, must be close.
    if len(request_times) >= 3:
        gaps = [request_times[i] - request_times[i - 1] for i in range(1, len(request_times))]
        gaps_ms = sorted(g * 1_000 for g in gaps)
        median_ms = gaps_ms[len(gaps_ms) // 2]
        assert 3_000 <= median_ms <= 8_000, (
            f"median poll gap {median_ms:.0f}ms is outside 3000-8000ms. "
            f"All gaps (ms, sorted): {[f'{g:.0f}' for g in gaps_ms]}. "
            "POLL_MS in admin.js may have changed, or the schedule is drifting."
        )


@pytest.mark.browser
def test_auth_status_contract_from_page(station_page, base_url: str) -> None:
    """/api/auth/status returns the shape the extension relies on.

    Calling from the page context (not httpx) means any future auth bridge
    that wraps the fetch in JS — e.g. via window.api — is also exercised.
    The #73 checkbox calls for a contract test; doing it from the JS
    runtime catches the failure mode operators would actually hit.
    """
    body = station_page.evaluate(
        """async (url) => {
          const r = await fetch(url, { credentials: 'include' });
          return { status: r.status, body: await r.json() };
        }""",
        f"{base_url}/api/auth/status",
    )

    assert body["status"] == 200, f"/api/auth/status returned {body['status']}: {body['body']!r}"
    payload = body["body"]
    assert "auth_enabled" in payload, (
        f"/api/auth/status missing 'auth_enabled' key — upstream webui contract drift. "
        f"Got keys: {sorted(payload.keys())}"
    )
    assert "logged_in" in payload, (
        f"/api/auth/status missing 'logged_in' key — upstream webui contract drift. "
        f"Got keys: {sorted(payload.keys())}"
    )
    # We're logged in via the storage_state fixture, so logged_in must be True.
    assert payload["logged_in"] is True, (
        f"expected logged_in=True (storage_state carries the session cookie), "
        f"got logged_in={payload['logged_in']!r}"
    )
