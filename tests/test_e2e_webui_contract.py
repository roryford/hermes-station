"""Contract test for hermes-webui's ``/api/auth/status`` response shape.

The auth bridge in ``hermes_station/admin/bridge_auth.py`` depends on
hermes-webui returning JSON of shape ``{auth_enabled: bool, logged_in: bool}``
from ``GET /api/auth/status``. If upstream ever drops or renames either
field the bridge silently rejects all sessions — this test catches that
drift loudly at CI/release time.

Skips automatically when ``HERMES_STATION_E2E_URL`` is unset so the default
host pytest run (no container) stays clean. Mirrors
``tests/test_e2e_auth_bridge.py``.

Run with:

    HERMES_STATION_E2E_URL=http://localhost:8787 \
    uv run pytest tests/test_e2e_webui_contract.py -v
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


def test_auth_status_response_shape(base_url: str) -> None:
    """``/api/auth/status`` must return a JSON object containing both
    ``auth_enabled`` and ``logged_in`` keys (booleans).

    This is the contract the auth bridge relies on. If this test fails after
    a hermes-webui upgrade, the bridge will silently reject all webui-session
    callers; the fix is either to pin webui or to update the bridge to the
    new contract.
    """
    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10.0) as client:
        resp = client.get("/api/auth/status")
    assert resp.status_code == 200, (
        f"expected 200 from /api/auth/status, got {resp.status_code}: {resp.text[:200]}"
    )
    ctype = resp.headers.get("content-type", "")
    assert "application/json" in ctype, f"expected JSON content-type, got: {ctype!r}"

    body = resp.json()
    assert isinstance(body, dict), f"expected JSON object, got {type(body).__name__}"

    # The two fields the bridge depends on.
    assert "auth_enabled" in body, (
        f"webui contract drift: /api/auth/status missing 'auth_enabled' — keys: {sorted(body.keys())}"
    )
    assert "logged_in" in body, (
        f"webui contract drift: /api/auth/status missing 'logged_in' — keys: {sorted(body.keys())}"
    )
    assert isinstance(body["auth_enabled"], bool), (
        f"'auth_enabled' must be bool, got {type(body['auth_enabled']).__name__}"
    )
    assert isinstance(body["logged_in"], bool), (
        f"'logged_in' must be bool, got {type(body['logged_in']).__name__}"
    )
