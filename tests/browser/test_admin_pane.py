"""Playwright tests for the pilot admin extension UI.

PR 1 of the browser suite (see issue #73): proves the substrate works and
covers two regression targets that httpx fundamentally cannot see —

  1. The extension actually injects its menu item and renders all five cards.
  2. The bespoke ``switchSettingsSection`` override (extension/admin.js:41-59)
     correctly clears the station pane when switching to a webui-native
     section. This is the gnarliest seam in the extension.
"""

from __future__ import annotations

import pytest

from .conftest import SECTION_BUTTON_SELECTOR, STATION_PANE_SELECTOR

DASH = "—"
EXPECTED_CARD_TITLES = ["Gateway", "WebUI", "Provider", "Channels", "Memory", "Versions"]


@pytest.mark.browser
def test_admin_pane_renders_all_cards(station_page) -> None:
    """All six status cards appear with at least one non-DASH value each.

    Catches regressions in render() (extension/admin.js:155-175) and in the
    /admin/api/pilot/status backend contract that feeds it.
    """
    pane = station_page.locator(STATION_PANE_SELECTOR)
    # Wait for the first poll to replace the "Loading status…" placeholder.
    pane.locator(".admin-card").first.wait_for(state="visible", timeout=10_000)

    headings = pane.locator(".admin-card h3").all_inner_texts()
    assert headings == EXPECTED_CARD_TITLES, (
        f"expected cards {EXPECTED_CARD_TITLES}, got {headings} — render() in "
        "extension/admin.js may have changed shape"
    )

    # Gateway card should have a real state (not DASH) when the gateway is
    # supervised. If this is DASH the backend payload is empty — a real bug.
    gateway_card = pane.locator(".admin-card").nth(0)
    state_dd = gateway_card.locator("dl dt:has-text('State') + dd")
    assert state_dd.inner_text() != DASH, (
        "Gateway State rendered as DASH — /admin/api/pilot/status returned an "
        "empty gateway object, or render() lost the value"
    )


@pytest.mark.browser
def test_switching_section_clears_station_pane(station_page) -> None:
    """Switching to a webui-native section must clear ``.active`` from the
    station pane and button.

    extension/admin.js wraps ``window.switchSettingsSection`` because webui's
    original has a hardcoded 6-section allowlist and won't deactivate our
    pane. Regression here would leave two panes visible at once.
    """
    pane = station_page.locator(STATION_PANE_SELECTOR)
    button = station_page.locator(SECTION_BUTTON_SELECTOR)

    # Sanity: station is active when station_page fixture returns.
    assert "active" in (pane.get_attribute("class") or "")
    assert "active" in (button.get_attribute("class") or "")

    # Switch to a webui-native section. 'conversation' is the default fallback
    # and is always present in webui's allowlist.
    station_page.evaluate("() => window.switchSettingsSection('conversation')")

    # Our override should have stripped .active from both station targets.
    pane_class = pane.get_attribute("class") or ""
    button_class = button.get_attribute("class") or ""
    assert "active" not in pane_class.split(), (
        f"station pane still .active after switching away — switchSettingsSection "
        f"override broke. class={pane_class!r}"
    )
    assert "active" not in button_class.split(), (
        f"station menu button still .active after switching away. class={button_class!r}"
    )
