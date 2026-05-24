"""Tests for the Hindsight memory sidecar.

Two invocation modes:

**From the test image** (HTTP-only tests, requires port 8888 exposed):

    # Boot runtime container with sidecar AND exposed port:
    container run -d --name hs-test -p 8787:8787 -p 8888:8888 \\
      -e HINDSIGHT_SIDECAR=1 -e OPENROUTER_API_KEY=local-fake-key \\
      -e HERMES_WEBUI_PASSWORD=test-admin-pw \\
      -e HERMES_ADMIN_PASSWORD=test-admin-pw \\
      hermes-station:local

    container run --rm \\
      -e HERMES_STATION_HINDSIGHT_SIDECAR=1 \\
      -e HERMES_STATION_HINDSIGHT_SIDECAR_URL=http://192.168.64.1:8888 \\
      -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \\
      -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \\
      -e HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \\
      hermes-station:test \\
      python -m pytest tests/test_hindsight_sidecar.py -v --no-cov

**From inside the runtime container** (all tests including process/log checks):

    container exec hs-test python -m pytest tests/test_hindsight_sidecar.py \\
      --no-cov -v
  (requires tests to be present inside the runtime container)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("HERMES_STATION_HINDSIGHT_SIDECAR"),
    reason="requires HERMES_STATION_HINDSIGHT_SIDECAR=1 and a running sidecar",
)

# Override with HERMES_STATION_HINDSIGHT_SIDECAR_URL when running from the test
# image (sidecar is in a different container, reachable via the host IP).
_SIDECAR_URL = os.environ.get("HERMES_STATION_HINDSIGHT_SIDECAR_URL", "http://localhost:8888")

# Process and log checks only work when running inside the runtime container.
_LOCAL = not os.environ.get("HERMES_STATION_HINDSIGHT_SIDECAR_URL")
_local_only = pytest.mark.skipif(
    not _LOCAL,
    reason="process/log checks require running inside the runtime container (no HERMES_STATION_HINDSIGHT_SIDECAR_URL set)",
)


def test_sidecar_api_responds() -> None:
    import httpx

    resp = httpx.get(f"{_SIDECAR_URL}/version", timeout=10)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text!r}"
    data = resp.json()
    assert "api_version" in data, f"'api_version' key missing from response: {data!r}"


def test_sidecar_api_version_is_valid() -> None:
    import httpx

    resp = httpx.get(f"{_SIDECAR_URL}/version", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "api_version" in data
    version = data["api_version"]
    assert isinstance(version, str) and version, f"api_version is empty or non-string: {version!r}"


def test_sidecar_banks_endpoint_reachable() -> None:
    import httpx

    resp = httpx.get(f"{_SIDECAR_URL}/v1/banks", timeout=10)
    # 200 (no auth required) or 401 (auth required) are both fine; 5xx is not.
    assert resp.status_code < 500, (
        f"unexpected server error from /v1/banks: status={resp.status_code} body={resp.text!r}"
    )


@_local_only
def test_sidecar_process_running() -> None:
    proc = subprocess.run(  # noqa: S603
        ["ps", "aux"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"ps aux failed: {proc.stderr!r}"
    assert "hindsight-api" in proc.stdout, (
        "hindsight-api process not found in ps aux output"
    )


@_local_only
def test_sidecar_pg0_running() -> None:
    proc = subprocess.run(  # noqa: S603
        ["ps", "aux"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"ps aux failed: {proc.stderr!r}"
    assert "postgres" in proc.stdout, (
        "postgres (pg0 embedded) process not found in ps aux output"
    )


@_local_only
def test_sidecar_log_exists_with_startup_marker() -> None:
    log_path = Path("/data/.hindsight/api.log")
    assert log_path.exists(), f"sidecar log not found at {log_path}"
    content = log_path.read_text(errors="replace")
    assert "Application startup complete" in content, (
        f"startup marker not found in {log_path}; log tail:\n{content[-2000:]!r}"
    )
