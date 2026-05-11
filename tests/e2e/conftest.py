"""Fixtures for browser-driven end-to-end tests.

These run against a real hermes-station container (the full WebUI subprocess
included), so the CSRF / Origin / Set-Cookie path is exercised by a real
browser instead of an httpx mock.

CI boots the container in `.github/workflows/ci.yml` (image job) and points
HERMES_STATION_E2E_URL at it. Locally:

    docker run --rm -d --name hs-e2e -p 8787:8787 \
        -e HERMES_WEBUI_PASSWORD=ci -e HERMES_ADMIN_PASSWORD=ci \
        hermes-station:latest
    HERMES_STATION_E2E_URL=http://127.0.0.1:8787 \
    HERMES_STATION_E2E_PASSWORD=ci \
        pytest tests/e2e
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("HERMES_STATION_E2E_URL")
    if not url:
        pytest.skip("HERMES_STATION_E2E_URL not set — e2e tests require a running container")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def webui_password() -> str:
    pw = os.environ.get("HERMES_STATION_E2E_PASSWORD")
    if not pw:
        pytest.skip("HERMES_STATION_E2E_PASSWORD not set")
    return pw
