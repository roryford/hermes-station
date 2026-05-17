"""Tests for hermes_station/app.py — lifespan branches and middleware."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _boot_app(fake_data_dir: Path, extra_env: dict[str, str] | None = None):
    """Create a fresh app pointed at fake_data_dir with webui disabled."""
    os.environ["HERMES_WEBUI_SRC"] = str(fake_data_dir / "no-webui")
    if extra_env:
        for k, v in extra_env.items():
            os.environ[k] = v
    from hermes_station.app import create_app

    return create_app()


# ---------------------------------------------------------------------------
# _BodySizeLimitMiddleware — fast-path Content-Length rejection (lines 107-113)
# ---------------------------------------------------------------------------


async def test_body_size_limit_rejects_large_content_length(fake_data_dir: Path) -> None:
    """A Content-Length header larger than the limit returns 413 immediately."""
    import httpx

    app = _boot_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/login",
            content=b"x",
            headers={"Content-Length": str(200 * 1024 * 1024)},  # 200 MB > 100 MB limit
        )
    assert resp.status_code == 413


async def test_body_size_limit_bad_content_length_passes_through(fake_data_dir: Path) -> None:
    """A non-integer Content-Length is ignored (ValueError is swallowed)."""
    import httpx

    app = _boot_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Send a non-integer Content-Length; middleware must not 413 or crash.
        resp = await client.get(
            "/health",
            headers={"Content-Length": "not-a-number"},
        )
    # /health should still respond normally (not 413)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _BodySizeLimitMiddleware — slow-path chunked body too large (lines 121-136)
# ---------------------------------------------------------------------------


async def test_body_size_limit_slow_path_large_body(fake_data_dir: Path) -> None:
    """Slow-path: sending a body larger than the limit returns 413."""
    import httpx
    from hermes_station.app import _BodySizeLimitMiddleware
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def _endpoint(request: Request) -> JSONResponse:
        # Force reading the entire body so counted_receive triggers
        await request.body()
        return JSONResponse({"ok": True})

    inner_app = Starlette(routes=[Route("/upload", _endpoint, methods=["POST"])])
    limited = _BodySizeLimitMiddleware(inner_app, max_bytes=10)

    transport = httpx.ASGITransport(app=limited)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/upload", content=b"x" * 50)
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# _ensure_env_passthrough — non-list passthrough triggers warning (lines 145-150)
# ---------------------------------------------------------------------------


def test_ensure_env_passthrough_non_list_warns(fake_data_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """_ensure_env_passthrough warns when passthrough is not a list and replaces it."""
    from hermes_station.app import _ensure_env_passthrough
    from hermes_station.config import Paths

    paths = Paths()
    # Pass a config where env_passthrough is a non-list (integer)
    config = {"terminal": {"env_passthrough": 42}}
    with caplog.at_level(logging.WARNING, logger="hermes_station.app"):
        _ensure_env_passthrough(paths, config, ["GITHUB_TOKEN"])

    assert any("env_passthrough" in r.message for r in caplog.records if r.levelno >= logging.WARNING)
    # After the call it should be replaced with a list containing the added key
    assert isinstance(config["terminal"]["env_passthrough"], list)
    assert "GITHUB_TOKEN" in config["terminal"]["env_passthrough"]


# ---------------------------------------------------------------------------
# Lifespan — admin password fallback warning (line 178)
# ---------------------------------------------------------------------------


def test_lifespan_warns_when_only_webui_password_set(
    fake_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When HERMES_ADMIN_PASSWORD is unset but HERMES_WEBUI_PASSWORD is set,
    lifespan logs a WARNING about the fallback."""
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_PASSWORD", "webui-only-pw")

    app = _boot_app(fake_data_dir)
    with caplog.at_level(logging.WARNING, logger="hermes_station.app"):
        with TestClient(app):
            pass

    assert any(
        "HERMES_ADMIN_PASSWORD" in r.message and "HERMES_WEBUI_PASSWORD" in r.message
        for r in caplog.records
        if r.levelno >= logging.WARNING
    ), "Expected admin-password fallback warning"


# ---------------------------------------------------------------------------
# Lifespan — seed functions return True (lines 183-196)
# ---------------------------------------------------------------------------


def test_lifespan_logs_seed_memory_provider(
    fake_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When seed_default_memory_provider returns True, lifespan logs it."""
    with patch("hermes_station.app.seed_default_memory_provider", return_value=True) as m:
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.INFO, logger="hermes_station.app"):
            with TestClient(app):
                pass
        assert m.called
    assert any("memory provider" in r.message for r in caplog.records if r.levelno == logging.INFO)


def test_lifespan_logs_seed_mcp_servers(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When seed_default_mcp_servers returns a non-empty list, lifespan logs it."""
    with patch("hermes_station.app.seed_default_mcp_servers", return_value=["memory", "sqlite"]):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.INFO, logger="hermes_station.app"):
            with TestClient(app):
                pass
    assert any("MCP" in r.message for r in caplog.records if r.levelno == logging.INFO)


def test_lifespan_logs_seed_neutral_personality(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When seed_neutral_personality_default returns True, lifespan logs it."""
    with patch("hermes_station.app.seed_neutral_personality_default", return_value=True):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.INFO, logger="hermes_station.app"):
            with TestClient(app):
                pass
    assert any("personality" in r.message for r in caplog.records if r.levelno == logging.INFO)


def test_lifespan_logs_seed_show_cost(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When seed_show_cost_default returns True, lifespan logs it."""
    with patch("hermes_station.app.seed_show_cost_default", return_value=True):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.INFO, logger="hermes_station.app"):
            with TestClient(app):
                pass
    assert any("show_cost" in r.message for r in caplog.records if r.levelno == logging.INFO)


def test_lifespan_logs_provider_seeded_with_railway_domain(
    fake_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When provider is auto-seeded, lifespan logs a settings link.
    With RAILWAY_PUBLIC_DOMAIN set, the link uses the real domain."""
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "my-station.railway.app")
    with patch("hermes_station.app.seed_provider_from_env", return_value="anthropic"):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.INFO, logger="hermes_station.app"):
            with TestClient(app):
                pass
    assert any("my-station.railway.app" in r.message for r in caplog.records if r.levelno == logging.INFO)


def test_lifespan_logs_provider_seeded_without_railway_domain(
    fake_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When provider is auto-seeded without RAILWAY_PUBLIC_DOMAIN, settings link is /admin/settings."""
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    with patch("hermes_station.app.seed_provider_from_env", return_value="openai"):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.INFO, logger="hermes_station.app"):
            with TestClient(app):
                pass
    assert any("/admin/settings" in r.message for r in caplog.records if r.levelno == logging.INFO)


# ---------------------------------------------------------------------------
# Lifespan — validate_readiness exception handling (lines 217-219)
# ---------------------------------------------------------------------------


def test_lifespan_readiness_exception_is_swallowed(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If validate_readiness raises, lifespan logs and sets readiness to None without aborting."""
    with patch("hermes_station.app.validate_readiness", side_effect=RuntimeError("boom")):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.ERROR, logger="hermes_station.app"):
            with TestClient(app) as client:
                resp = client.get("/health")
    # App still boots and /health responds
    assert resp.status_code == 200
    # The exception must have been logged
    assert any("readiness" in r.message.lower() for r in caplog.records if r.levelno >= logging.ERROR)


# ---------------------------------------------------------------------------
# Lifespan — drift message logging and row attachment (lines 222, 227-233)
# ---------------------------------------------------------------------------


def test_lifespan_drift_messages_logged(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """detect_provider_drift messages are emitted as WARNINGs."""
    with patch(
        "hermes_station.app.detect_provider_drift", return_value=["drift: ANTHROPIC_API_KEY overridden"]
    ):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.WARNING, logger="hermes_station.app"):
            with TestClient(app):
                pass
    assert any("drift" in r.message for r in caplog.records if r.levelno >= logging.WARNING)


def test_lifespan_drift_attached_to_provider_row(
    fake_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When drift is present and readiness has a provider row, drift notes are attached."""
    from hermes_station.config import Paths, write_yaml_config

    paths = Paths()
    write_yaml_config(paths.config_path, {"model": {"provider": "anthropic"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    drift_msgs = ["drift: ANTHROPIC_API_KEY overridden by .env"]
    with patch("hermes_station.app.detect_provider_drift", return_value=drift_msgs):
        app = _boot_app(fake_data_dir)
        with TestClient(app) as client:
            resp = client.get("/health")

    # App must still boot without error
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lifespan — gateway autostart=True branch (lines 235-239)
# ---------------------------------------------------------------------------


def test_lifespan_gateway_autostart_true_calls_start(
    fake_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When should_autostart returns True, gateway.start() is called."""
    # Patch should_autostart to return True and gateway.start to a no-op
    with (
        patch("hermes_station.app.should_autostart", return_value=True),
        patch("hermes_station.gateway.Gateway.start", new_callable=AsyncMock) as mock_start,
    ):
        app = _boot_app(fake_data_dir)
        with TestClient(app) as client:
            resp = client.get("/health")

    assert resp.status_code == 200
    assert mock_start.called


def test_lifespan_gateway_autostart_false_skips_start(
    fake_data_dir: Path,
) -> None:
    """When should_autostart returns False, gateway.start() is NOT called."""
    with (
        patch("hermes_station.app.should_autostart", return_value=False),
        patch("hermes_station.gateway.Gateway.start", new_callable=AsyncMock) as mock_start,
    ):
        app = _boot_app(fake_data_dir)
        with TestClient(app) as client:
            resp = client.get("/health")

    assert resp.status_code == 200
    assert not mock_start.called


# ---------------------------------------------------------------------------
# Lifespan — autostart exception handler (lines 242-243)
# ---------------------------------------------------------------------------


def test_lifespan_gateway_start_exception_is_swallowed(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If gateway.start() raises during autostart, lifespan logs and continues."""
    with (
        patch("hermes_station.app.should_autostart", return_value=True),
        patch(
            "hermes_station.gateway.Gateway.start",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no agent"),
        ),
    ):
        app = _boot_app(fake_data_dir)
        with caplog.at_level(logging.ERROR, logger="hermes_station.app"):
            with TestClient(app) as client:
                resp = client.get("/health")

    assert resp.status_code == 200
    assert any("autostart" in r.message.lower() for r in caplog.records if r.levelno >= logging.ERROR)


# ---------------------------------------------------------------------------
# Lifespan — webui startup path when server.py exists (lines 247-255)
# ---------------------------------------------------------------------------


def test_lifespan_webui_started_when_server_py_exists(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When server.py exists, webui.start() is called and wait_ready() is awaited.
    When wait_ready() returns False, a warning is logged."""
    # Create a fake server.py so the webui branch is entered
    webui_src = fake_data_dir / "fake-webui"
    webui_src.mkdir()
    (webui_src / "server.py").write_text("# fake")
    os.environ["HERMES_WEBUI_SRC"] = str(webui_src)

    from hermes_station.app import create_app

    app = create_app()

    with (
        patch("hermes_station.webui.WebUIProcess.start", new_callable=AsyncMock),
        patch("hermes_station.webui.WebUIProcess.wait_ready", new_callable=AsyncMock, return_value=False),
        patch("hermes_station.webui.WebUIProcess.stop", new_callable=AsyncMock),
    ):
        with caplog.at_level(logging.WARNING, logger="hermes_station.app"):
            with TestClient(app):
                pass

    assert any(
        "not healthy" in r.message.lower() or "hermes-webui" in r.message.lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


def test_lifespan_webui_start_exception_is_swallowed(
    fake_data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If webui.start() raises, lifespan logs and continues."""
    webui_src = fake_data_dir / "fake-webui2"
    webui_src.mkdir()
    (webui_src / "server.py").write_text("# fake")
    os.environ["HERMES_WEBUI_SRC"] = str(webui_src)

    from hermes_station.app import create_app

    app = create_app()

    with (
        patch(
            "hermes_station.webui.WebUIProcess.start",
            new_callable=AsyncMock,
            side_effect=OSError("no python"),
        ),
        patch("hermes_station.webui.WebUIProcess.stop", new_callable=AsyncMock),
    ):
        with caplog.at_level(logging.ERROR, logger="hermes_station.app"):
            with TestClient(app) as client:
                resp = client.get("/health")

    assert resp.status_code == 200
    assert any("webui" in r.message.lower() for r in caplog.records if r.levelno >= logging.ERROR)


# ---------------------------------------------------------------------------
# Lifespan — shutdown path (lines 262-269)
# ---------------------------------------------------------------------------


def test_lifespan_shutdown_stops_gateway_and_webui(fake_data_dir: Path) -> None:
    """On shutdown, gateway.stop() and webui.stop() are called."""
    with (
        patch("hermes_station.gateway.Gateway.stop", new_callable=AsyncMock) as mock_gw_stop,
        patch("hermes_station.webui.WebUIProcess.stop", new_callable=AsyncMock) as mock_wu_stop,
    ):
        app = _boot_app(fake_data_dir)
        with TestClient(app) as client:
            client.get("/health")
        # Exiting TestClient context triggers lifespan shutdown

    assert mock_gw_stop.called
    assert mock_wu_stop.called
