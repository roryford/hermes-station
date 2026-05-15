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


def test_health_ready_503_when_intended_missing(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
