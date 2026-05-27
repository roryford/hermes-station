"""Shared pytest fixtures for container-level e2e tests."""

from __future__ import annotations

import os

import pytest


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
