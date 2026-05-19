"""Playwright fixtures for the station extension UI tests.

Substrate for PR 1 of the browser suite (see issue #73). Runs against a live
container reached via ``HERMES_STATION_E2E_URL``; auto-skips when the var is
unset so the default host pytest run stays clean.

Design notes
------------

* **Per-worker login.** Logging in once per pytest-xdist worker (not per test)
  keeps the parallel run fast. The session-scoped ``_storage_state_path``
  fixture writes a Playwright storage_state JSON to a worker-unique tmp path;
  the ``browser_context_args`` override loads it into every test's context.

* **No shared mutable state between workers.** Each worker logs in via its
  own browser instance and writes its own storage_state file. xdist gives us
  ``worker_id`` ("gw0", "gw1", ...) or "master" for serial runs.

* **Onboarding overlay.** hermes-webui shows an onboarding overlay on first
  load that intercepts clicks. It re-appears on every fresh page load (not
  localStorage-driven across sessions), so the ``station_page`` fixture
  dismisses it per-page rather than once per worker.

* **CSP bypass.** webui ships an enforced ``script-src`` without
  ``'unsafe-eval'``. Playwright's ``wait_for_function`` injects predicates
  via ``new Function`` and would be blocked, so contexts are created with
  ``bypass_csp=True``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

try:
    from playwright.sync_api import Browser, Page, sync_playwright
except ImportError:  # pragma: no cover — handled by the skip below
    sync_playwright = None  # type: ignore[assignment]
    Browser = Page = None  # type: ignore[assignment, misc]


SECTION_BUTTON_SELECTOR = '#settingsMenu .side-menu-item[data-settings-section="station"]'
STATION_PANE_SELECTOR = "#settingsPaneAdmin"


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("HERMES_STATION_E2E_URL")
    if not url:
        pytest.skip("HERMES_STATION_E2E_URL not set — browser suite requires a running container")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def webui_password() -> str:
    pw = os.environ.get("HERMES_STATION_E2E_PASSWORD")
    if not pw:
        pytest.skip("HERMES_STATION_E2E_PASSWORD not set")
    return pw


@pytest.fixture(scope="session")
def _playwright_instance() -> Iterator[object]:
    if sync_playwright is None:
        pytest.skip("playwright is not installed — install with `uv pip install playwright pytest-playwright`")
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(_playwright_instance) -> Iterator["Browser"]:
    """One browser per worker. Headless; reuse across tests in the worker."""
    b = _playwright_instance.chromium.launch(headless=True)
    try:
        yield b
    finally:
        b.close()


@pytest.fixture(scope="session")
def _storage_state_path(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
    browser: "Browser",
    base_url: str,
    webui_password: str,
) -> Path:
    """Log in once per worker, save storage_state for reuse.

    Dismisses the onboarding overlay if present so subsequent test contexts
    that load this state don't have it covering the settings menu.
    """
    # Worker-unique path. tmp_path_factory.mktemp() already gives per-worker
    # isolation under xdist, but include worker_id explicitly for clarity.
    state_dir = tmp_path_factory.mktemp(f"station_browser_state_{worker_id}")
    state_file = state_dir / "storage_state.json"

    # bypass_csp=True is required: hermes-webui ships an enforced CSP without
    # 'unsafe-eval', which breaks Playwright's wait_for_function (it injects
    # the predicate via `new Function`). bypass_csp is a Playwright knob that
    # disables CSP at the network layer for the context only.
    context = browser.new_context(base_url=base_url, bypass_csp=True)
    try:
        # Log in via the JSON API rather than the SPA form: simpler, faster,
        # and the form's success handler is JS-driven (no server redirect),
        # which makes `wait_for_url` flaky. Playwright's request API shares
        # the context's cookie jar, so the session cookie persists into pages.
        resp = context.request.post(
            f"{base_url}/api/auth/login",
            headers={"Origin": base_url, "Content-Type": "application/json"},
            data=json.dumps({"password": webui_password}),
        )
        if resp.status != 200:
            raise RuntimeError(
                f"login POST failed: {resp.status} {resp.text()!r} — "
                "check HERMES_STATION_E2E_PASSWORD"
            )
        context.storage_state(path=str(state_file))
    finally:
        context.close()

    return state_file


def _dismiss_onboarding(page: "Page") -> None:
    """Best-effort: close the onboarding overlay if it is visible.

    webui exposes a ``skipOnboarding()`` global; calling it both hides the
    overlay and persists dismissal state. We wait for the function to be
    defined (it loads with the main webui bundle) and call it. Falls back to
    hiding the element directly if the function isn't present.
    """
    try:
        page.wait_for_function("typeof window.skipOnboarding === 'function'", timeout=5_000)
        page.evaluate("() => window.skipOnboarding()")
    except Exception:
        page.evaluate(
            """() => {
              const el = document.getElementById('onboardingOverlay');
              if (el) { el.style.display = 'none'; el.setAttribute('aria-hidden', 'true'); }
            }"""
        )
    # Best-effort wait for the overlay to actually leave the layout.
    try:
        page.locator("#onboardingOverlay").wait_for(state="hidden", timeout=2_000)
    except Exception:
        pass


@pytest.fixture
def browser_context_args(_storage_state_path: Path, base_url: str) -> dict:
    """Plugin-recognised fixture: pytest-playwright passes this to new_context.

    Loading the per-worker storage_state keeps every test logged in without
    re-doing the form-fill dance.
    """
    return {
        "base_url": base_url,
        "storage_state": str(_storage_state_path),
        "viewport": {"width": 1280, "height": 800},
        # See note in _storage_state_path: webui's enforced CSP blocks
        # 'unsafe-eval', which breaks Playwright's wait_for_function helpers.
        "bypass_csp": True,
    }


@pytest.fixture
def context(browser: "Browser", browser_context_args: dict):
    ctx = browser.new_context(**browser_context_args)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def page(context) -> Iterator["Page"]:
    p = context.new_page()
    try:
        yield p
    finally:
        p.close()


@pytest.fixture
def station_page(page: "Page", base_url: str) -> "Page":
    """Navigate to /, wait for the station extension to inject its menu item,
    click it, and return the page with the station pane active.
    """
    page.goto(f"{base_url}/", wait_until="load")
    # Onboarding overlay re-appears on every fresh page load (it isn't
    # localStorage-driven across sessions); dismiss it before clicking.
    _dismiss_onboarding(page)
    # Wait for the extension to have fully bootstrapped: the menu button is
    # appended *and* the wrapped switchSettingsSection accepts 'station'.
    # Under concurrent load the button can appear before window.api / webui's
    # own scripts finish, so a presence check alone isn't enough.
    page.wait_for_selector(SECTION_BUTTON_SELECTOR, state="attached", timeout=10_000)
    page.wait_for_function(
        "typeof window.switchPanel === 'function' && "
        "typeof window.switchSettingsSection === 'function'",
        timeout=10_000,
    )
    page.evaluate("() => window.switchPanel('settings')")
    # Activate the station pane via the same code path webui's menu buttons
    # use. Clicking the button works too but adds a layer (visibility,
    # actionability checks) that has flaked under parallel load.
    page.evaluate("() => window.switchSettingsSection('station')")
    page.wait_for_selector(f"{STATION_PANE_SELECTOR}.active", state="attached", timeout=10_000)
    return page


# Re-export so test modules can `from .conftest import ...` if needed.
__all__ = ["SECTION_BUTTON_SELECTOR", "STATION_PANE_SELECTOR"]
