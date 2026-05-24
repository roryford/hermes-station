"""In-container tests for the Hindsight memory sidecar.

Run these from inside the test image after booting the runtime container
with HINDSIGHT_SIDECAR=1 and OPENROUTER_API_KEY set:

    container run --rm \\
      -e HERMES_STATION_HINDSIGHT_SIDECAR=1 \\
      -e HERMES_STATION_REQUIRE_TOOLBELT=1 \\
      -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \\
      -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \\
      -e HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \\
      hermes-station:test \\
      python -m pytest tests/test_hindsight_sidecar.py -v --no-cov

The runtime container must be booted with:
    -e HINDSIGHT_SIDECAR=1 -e OPENROUTER_API_KEY=local-fake-key
(a fake key is sufficient — the API server starts and listens even if LLM
auth would fail with 401)
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

_SIDECAR_URL = "http://localhost:8888"


def test_sidecar_api_responds() -> None:
    import httpx

    resp = httpx.get(f"{_SIDECAR_URL}/version", timeout=10)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text!r}"
    data = resp.json()
    assert "api_version" in data, f"'api_version' key missing from response: {data!r}"


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


def test_sidecar_log_exists_with_startup_marker() -> None:
    log_path = Path("/data/.hindsight/api.log")
    assert log_path.exists(), f"sidecar log not found at {log_path}"
    content = log_path.read_text(errors="replace")
    assert "Application startup complete" in content, (
        f"startup marker not found in {log_path}; log tail:\n{content[-2000:]!r}"
    )


def test_sidecar_api_config_shows_expected_providers() -> None:
    import httpx

    resp = httpx.get(f"{_SIDECAR_URL}/version", timeout=10)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text!r}"
    data = resp.json()
    assert isinstance(data, dict), f"expected a JSON object, got {type(data).__name__}: {data!r}"
    assert "api_version" in data, f"'api_version' key missing from response: {data!r}"


def test_sidecar_banks_endpoint_reachable() -> None:
    import httpx

    resp = httpx.get(f"{_SIDECAR_URL}/v1/banks", timeout=10)
    assert resp.status_code != 500, (
        f"sidecar /v1/banks returned 500 (server error): {resp.text!r}"
    )
    # 200 (no auth) or 401 (auth required) are both acceptable; 5xx is not.
    assert resp.status_code < 500, (
        f"unexpected server error from /v1/banks: status={resp.status_code} body={resp.text!r}"
    )
