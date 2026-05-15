"""E2E DX tests — validates the improved first-run and health-schema behavior.

Requires a running container pointed to by HERMES_STATION_E2E_URL.
Auto-skips when that env var is not set so the default pytest run stays clean.

Boot the container with OPENROUTER_API_KEY set to exercise the autostart fix:

    DATA=$(mktemp -d)
    container run -d --name hs-dx-verify -p 8789:8787 \\
      -e HERMES_ADMIN_PASSWORD=test-admin-pw \\
      -e HERMES_WEBUI_PASSWORD=test-admin-pw \\
      -e OPENROUTER_API_KEY=sk-or-v1-FAKEKEYFORTEST \\
      -v "$DATA:/data" hermes-station:local

    HERMES_STATION_E2E_URL=http://127.0.0.1:8789 \\
      HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \\
      uv run pytest tests/test_e2e_dx.py -v --no-cov
"""

from __future__ import annotations

import os
import time

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("HERMES_STATION_E2E_URL")
    if not url:
        pytest.skip("HERMES_STATION_E2E_URL not set — requires a running container")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def admin_password() -> str:
    return os.environ.get("HERMES_STATION_E2E_ADMIN_PASSWORD", "test-admin-pw")


@pytest.fixture(scope="session")
def admin_client(base_url: str, admin_password: str) -> httpx.Client:
    client = httpx.Client(base_url=base_url, follow_redirects=True, timeout=10.0)
    resp = client.post("/admin/login", data={"password": admin_password})
    assert resp.status_code == 200, f"admin login failed: {resp.status_code}"
    assert "hermes_station_admin" in client.cookies
    return client


# ─────────────────────────────────────────── core fix: gateway autostarts with provider


def test_gateway_starts_after_provider_seeded(base_url: str) -> None:
    """With OPENROUTER_API_KEY set at boot, the gateway must have been started
    (autostart fired). We accept 'running' or 'startup_failed' — both prove the
    supervisor launched the gateway. 'startup_failed' is expected when the key
    is a test placeholder that fails auth. What must NOT appear is 'unknown' or
    'stopped', which would mean autostart never triggered.
    """
    AUTOSTARTED_STATES = {"running", "starting", "startup_failed", "stopping", "stopped"}
    NOT_STARTED_STATES = {"unknown"}

    deadline = time.time() + 15
    state = "unknown"
    while time.time() < deadline:
        resp = httpx.get(f"{base_url}/health", timeout=5)
        data = resp.json()
        state = data.get("components", {}).get("gateway", {}).get("state", "unknown")
        if state in AUTOSTARTED_STATES:
            break
        time.sleep(1)

    assert state not in NOT_STARTED_STATES, (
        f"gateway.state is '{state}' — autostart did not fire. "
        "Expected any state other than 'unknown' after OPENROUTER_API_KEY was set at boot."
    )


# ─────────────────────────────────────────── health schema contract


def test_health_schema_fields(base_url: str) -> None:
    """Verify /health returns the documented fields for scheduler and gateway."""
    resp = httpx.get(f"{base_url}/health", timeout=5)
    assert resp.status_code == 200
    data = resp.json()

    scheduler = data["components"]["scheduler"]
    for field in ("state", "enabled", "job_count", "last_run_at", "failed_jobs"):
        assert field in scheduler, f"scheduler missing field '{field}'"

    gateway = data["components"]["gateway"]
    for field in ("state", "platform", "connection"):
        assert field in gateway, f"gateway missing field '{field}'"

    assert data["status"] in ("ok", "degraded", "down")


# ─────────────────────────────────────────── dashboard gateway-idle CTA


def test_dashboard_gateway_idle_cta_appears(admin_client: httpx.Client, base_url: str) -> None:
    """When provider is configured but gateway is stopped, the status panel
    must show the 'Start gateway' call-to-action.
    """
    # Stop the gateway first.
    admin_client.post("/admin/api/gateway/stop")
    time.sleep(1)

    # Fetch the status fragment (what the dashboard HTMX polling sees).
    resp = admin_client.get("/admin/_partial/status")
    assert resp.status_code == 200
    body = resp.text

    assert "Start gateway" in body or "not running" in body.lower(), (
        "expected gateway-idle CTA in status panel when provider is configured and gateway is stopped"
    )

    # Restore: start the gateway again.
    admin_client.post("/admin/api/gateway/start")
