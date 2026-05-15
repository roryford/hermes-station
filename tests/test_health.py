"""Endpoint tests for /health, /health/live, /health/ready."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient


def _build_app(fake_data_dir: Path):
    # Avoid the hermes-webui supervisor actually trying to start a subprocess
    # during lifespan — point HERMES_WEBUI_SRC at an empty dir so app.py takes
    # the "source not found" branch.
    import os

    os.environ["HERMES_WEBUI_SRC"] = str(fake_data_dir / "no-webui")
    from hermes_station.app import create_app

    return create_app()


def test_health_live_returns_200_alive(fake_data_dir: Path) -> None:
    app = _build_app(fake_data_dir)
    with TestClient(app) as client:
        resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_health_full_returns_payload_shape(fake_data_dir: Path) -> None:
    app = _build_app(fake_data_dir)
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded", "down"}
    components = body["components"]
    assert components["control_plane"]["state"] == "ready"
    for k in ("webui", "gateway", "scheduler", "storage", "memory"):
        assert k in components
    assert "readiness" in body
    assert "versions" in body
    assert body["versions"]["python"]
    assert body["versions"]["hermes_station"]


def test_health_ready_503_when_intended_missing(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed a config that intends to use Anthropic but no key in env or .env.
    from hermes_station.config import Paths, write_yaml_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    paths = Paths()
    write_yaml_config(
        paths.config_path,
        {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}},
    )

    app = _build_app(fake_data_dir)
    with TestClient(app) as client:
        resp = client.get("/health/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    row = body["readiness"]["provider:anthropic"]
    assert row["intended"] is True
    assert row["ready"] is False


def test_health_ready_200_when_no_intended_capabilities(fake_data_dir: Path) -> None:
    """A vanilla config has nothing intended that fails → ready returns 200/ok."""
    app = _build_app(fake_data_dir)
    with TestClient(app) as client:
        resp = client.get("/health/ready")
    # The default seeded memory.provider=holographic is intended AND ready
    # (path writable on the temp fixture), so status should be "ok".
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_full_always_200_even_when_degraded(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hermes_station.config import Paths, write_yaml_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    paths = Paths()
    write_yaml_config(paths.config_path, {"model": {"provider": "anthropic"}})

    app = _build_app(fake_data_dir)
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_gateway_snapshot_shape(tmp_path: Path) -> None:
    from hermes_station.gateway import Gateway

    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert set(snap.keys()) >= {"state", "platform", "connection", "is_running", "is_healthy"}
    assert snap["state"] == "unknown"
    assert snap["connection"] == "not_configured"
    assert snap["is_running"] is False


async def test_gateway_snapshot_connected_when_recent(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timezone

    from hermes_station.gateway import Gateway

    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(
        json.dumps(
            {
                "gateway_state": "running",
                "platform": "telegram",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["state"] == "running"
    assert snap["platform"] == "telegram"
    assert snap["connection"] == "connected"


async def test_gateway_snapshot_token_invalid_on_auth_error(tmp_path: Path) -> None:
    import json

    from hermes_station.gateway import Gateway

    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(
        json.dumps(
            {
                "gateway_state": "startup_failed",
                "last_error": "401 Unauthorized: invalid token",
            }
        )
    )
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["connection"] == "token_invalid"


async def test_webui_snapshot_down_when_not_running(tmp_path: Path) -> None:
    from hermes_station.webui import WebUIProcess

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    snap = proc.snapshot()
    assert snap["state"] == "down"
    assert snap["pid"] is None
    assert snap["is_running"] is False
    assert snap["internal_url"].startswith("http://")


async def test_webui_snapshot_disabled(tmp_path: Path) -> None:
    from hermes_station.webui import WebUIProcess

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    proc.mark_disabled()
    snap = proc.snapshot()
    assert snap["state"] == "disabled"


# ---------------------------------------------------------------------------
# Scheduler block unit tests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Gateway failure signal passthrough tests
# ---------------------------------------------------------------------------


async def test_gateway_snapshot_no_failure_signals_when_absent(tmp_path: Path) -> None:
    """Failure signal keys must be absent (not None) when not in state file."""
    import json

    from hermes_station.gateway import Gateway

    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(json.dumps({"gateway_state": "running"}))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert "last_auth_failure_at" not in snap
    assert "last_crash_at" not in snap
    assert "last_error_at" not in snap


async def test_gateway_snapshot_passes_through_failure_signals(tmp_path: Path) -> None:
    """Known failure signal keys are forwarded verbatim from gateway_state.json."""
    import json

    from hermes_station.gateway import Gateway

    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(
        json.dumps(
            {
                "gateway_state": "startup_failed",
                "last_auth_failure_at": "2026-05-15T09:00:00+00:00",
                "last_crash_at": "2026-05-15T08:55:00+00:00",
            }
        )
    )
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["last_auth_failure_at"] == "2026-05-15T09:00:00+00:00"
    assert snap["last_crash_at"] == "2026-05-15T08:55:00+00:00"
    assert "last_error_at" not in snap  # not in file → not in snapshot


# ---------------------------------------------------------------------------
# Scheduler block unit tests
# ---------------------------------------------------------------------------


def test_scheduler_block_unknown_when_no_files(tmp_path: Path) -> None:
    from hermes_station.health import _scheduler_block

    class FakePaths:
        hermes_home = tmp_path

    block = _scheduler_block(FakePaths())
    assert block["state"] == "unknown"
    assert block["enabled"] is False
    assert block["last_run_at"] is None
    assert block["failed_jobs"] is None
    assert block["job_count"] is None


def test_scheduler_block_configured_from_cron_jobs_list(tmp_path: Path) -> None:
    """cron/jobs.json present (list format) but no state file → state=configured."""
    import json

    from hermes_station.health import _scheduler_block

    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text(json.dumps([{"id": "job1"}, {"id": "job2"}, {"id": "job3"}]))

    class FakePaths:
        hermes_home = tmp_path

    block = _scheduler_block(FakePaths())
    assert block["state"] == "configured"
    assert block["enabled"] is True
    assert block["job_count"] == 3
    assert block["last_run_at"] is None


def test_scheduler_block_configured_from_cron_jobs_dict(tmp_path: Path) -> None:
    """cron/jobs.json in dict format (keyed by job id) → job_count is len of dict."""
    import json

    from hermes_station.health import _scheduler_block

    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text(json.dumps({"daily": {}, "hourly": {}}))

    class FakePaths:
        hermes_home = tmp_path

    block = _scheduler_block(FakePaths())
    assert block["state"] == "configured"
    assert block["job_count"] == 2


def test_scheduler_block_ready_from_state_file(tmp_path: Path) -> None:
    """scheduler_state.json present but no cron/jobs.json → state=ready."""
    import json

    from hermes_station.health import _scheduler_block

    (tmp_path / "scheduler_state.json").write_text(
        json.dumps({"last_run_at": "2026-05-15T10:00:00+00:00", "failed_jobs": 0})
    )

    class FakePaths:
        hermes_home = tmp_path

    block = _scheduler_block(FakePaths())
    assert block["state"] == "ready"
    assert block["enabled"] is False  # no cron/jobs.json → unknown if jobs exist
    assert block["last_run_at"] == "2026-05-15T10:00:00+00:00"
    assert block["failed_jobs"] == 0
    assert block["job_count"] is None


def test_scheduler_block_ready_with_both_files(tmp_path: Path) -> None:
    """Both files present → state=ready (state file wins) and job_count set."""
    import json

    from hermes_station.health import _scheduler_block

    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text(json.dumps([{"id": "j1"}]))
    (tmp_path / "scheduler_state.json").write_text(
        json.dumps({"last_run_at": "2026-05-15T12:00:00+00:00", "failed_jobs": 1})
    )

    class FakePaths:
        hermes_home = tmp_path

    block = _scheduler_block(FakePaths())
    assert block["state"] == "ready"
    assert block["enabled"] is True
    assert block["job_count"] == 1
    assert block["failed_jobs"] == 1


def test_scheduler_block_tolerates_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON in either file is swallowed; state stays unknown."""
    from hermes_station.health import _scheduler_block

    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text("not json {{{")
    (tmp_path / "scheduler_state.json").write_text("also bad")

    class FakePaths:
        hermes_home = tmp_path

    block = _scheduler_block(FakePaths())
    assert block["state"] == "unknown"
    assert block["job_count"] is None


# ---------------------------------------------------------------------------
# health.py pure helper unit tests (from test_coverage_boost.py)
# ---------------------------------------------------------------------------


def test_storage_block_writable_path(tmp_path: Path) -> None:
    from hermes_station.health import _storage_block

    class FakePaths:
        home = tmp_path
        config_path = tmp_path / "config.yaml"

    block = _storage_block(FakePaths())
    assert block["data_writable"] is True
    assert block["config_readable"] is True


def test_storage_block_reads_existing_config(tmp_path: Path) -> None:
    from hermes_station.health import _storage_block

    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: anthropic\n")

    class FakePaths:
        home = tmp_path
        config_path = config

    block = _storage_block(FakePaths())
    assert block["config_readable"] is True


def test_memory_block_with_holographic_ready(tmp_path: Path) -> None:
    from hermes_station.health import _memory_block

    class FakeRow:
        ready = True

    class FakeReadiness:
        readiness = {"memory:holographic": FakeRow()}

    block = _memory_block(FakeReadiness(), None)
    assert block["provider"] == "holographic"
    assert block["db_ok"] is True


def test_memory_block_no_memory_row(tmp_path: Path) -> None:
    """No memory:* row → falls back to builtin."""
    from hermes_station.health import _memory_block

    class FakeReadiness:
        readiness = {}

    block = _memory_block(FakeReadiness(), None)
    assert block["provider"] == "builtin"
    assert block["db_ok"] is True


def test_memory_block_readiness_none() -> None:
    from hermes_station.health import _memory_block

    block = _memory_block(None, None)
    assert block["provider"] == "none"
    assert block["db_ok"] is True


def test_readiness_to_payload_with_dict_readiness() -> None:
    from hermes_station.health import _readiness_to_payload

    payload = {"cap_a": {"intended": True, "ready": False}}
    result = _readiness_to_payload(payload)
    assert result == payload


def test_readiness_to_payload_with_none() -> None:
    from hermes_station.health import _readiness_to_payload

    assert _readiness_to_payload(None) == {}


def test_readiness_to_payload_with_unknown_type() -> None:
    from hermes_station.health import _readiness_to_payload

    assert _readiness_to_payload("not a readiness") == {}


def test_versions_payload_none() -> None:
    from hermes_station.health import _versions_payload

    assert _versions_payload(None) == {}


def test_gateway_snapshot_health_no_gateway() -> None:
    from hermes_station.health import _gateway_snapshot

    class FakeState:
        gateway = None

    snap = _gateway_snapshot(FakeState())
    assert snap["state"] == "disabled"
    assert snap["connection"] == "not_configured"


def test_gateway_snapshot_health_with_gateway(tmp_path: Path) -> None:
    from hermes_station.gateway import Gateway
    from hermes_station.health import _gateway_snapshot

    gw = Gateway(hermes_home=tmp_path)

    class FakeState:
        gateway = gw

    snap = _gateway_snapshot(FakeState())
    assert "state" in snap
    assert "connection" in snap


def test_webui_snapshot_none() -> None:
    from hermes_station.health import _webui_snapshot

    class FakeState:
        webui = None

    snap = _webui_snapshot(FakeState())
    assert snap["state"] == "disabled"
    assert snap["pid"] is None


def test_compose_status_down_when_storage_not_writable() -> None:
    from hermes_station.health import _compose_status

    status = _compose_status(
        storage={"data_writable": False},
        readiness=None,
        webui={"state": "ready"},
    )
    assert status == "down"


def test_compose_status_degraded_dict_readiness() -> None:
    from hermes_station.health import _compose_status

    readiness = {
        "readiness": {"provider:anthropic": {"intended": True, "ready": False}}
    }
    status = _compose_status(
        storage={"data_writable": True},
        readiness=readiness,
        webui={"state": "ready"},
    )
    assert status == "degraded"


def test_compose_status_degraded_when_webui_not_ready() -> None:
    from hermes_station.health import _compose_status

    status = _compose_status(
        storage={"data_writable": True},
        readiness=None,
        webui={"state": "down"},
    )
    assert status == "degraded"


def test_compose_status_ok() -> None:
    from hermes_station.health import _compose_status

    status = _compose_status(
        storage={"data_writable": True},
        readiness=None,
        webui={"state": "ready"},
    )
    assert status == "ok"


def test_scheduler_job_count_in_health_payload(fake_data_dir: Path) -> None:
    """job_count is exposed in the /health response under components.scheduler."""
    import json
    import os

    os.environ["HERMES_WEBUI_SRC"] = str(fake_data_dir / "no-webui")
    from hermes_station.app import create_app
    from hermes_station.config import Paths

    paths = Paths()
    cron_dir = paths.hermes_home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "jobs.json").write_text(json.dumps([{"id": "test-job"}]))

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    scheduler = resp.json()["components"]["scheduler"]
    assert scheduler["job_count"] == 1
    assert scheduler["state"] in {"configured", "ready"}
