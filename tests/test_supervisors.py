"""Unit tests for Phase 1 supervisor + proxy modules.

The actual subprocess / asyncio.Task lifecycle is exercised end-to-end via the
container smoke test (`scripts/smoke.sh` and `.github/workflows/ci.yml`). These
tests cover the pure-function pieces that don't need a real subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_station.gateway import Gateway, should_autostart
from hermes_station.proxy import _filter_request_headers, _strip_our_cookies
from hermes_station.webui import WebUIProcess


# ───────────────────────────────────────────────────── gateway.should_autostart


def test_should_autostart_forced_on() -> None:
    assert should_autostart(mode="1", config={}, env_values={}) is True
    assert should_autostart(mode="true", config={}, env_values={}) is True
    assert should_autostart(mode="on", config={}, env_values={}) is True
    assert should_autostart(mode="YES", config={}, env_values={}) is True


def test_should_autostart_forced_off() -> None:
    assert should_autostart(mode="0", config={}, env_values={}) is False
    assert should_autostart(mode="false", config={}, env_values={}) is False
    assert should_autostart(mode="off", config={}, env_values={}) is False


def test_should_autostart_auto_requires_provider_and_channel() -> None:
    # No provider configured
    assert (
        should_autostart(mode="auto", config={}, env_values={"TELEGRAM_BOT_TOKEN": "abc"})
        is False
    )
    # Provider set, but env var missing
    config = {"model": {"provider": "anthropic", "default": "claude-sonnet-4.6"}}
    assert (
        should_autostart(mode="auto", config=config, env_values={"TELEGRAM_BOT_TOKEN": "abc"})
        is False
    )
    # Provider configured with key but no channel
    env = {"ANTHROPIC_API_KEY": "sk-ant-xxx"}
    assert should_autostart(mode="auto", config=config, env_values=env) is False
    # Provider configured AND channel configured
    env = {"ANTHROPIC_API_KEY": "sk-ant-xxx", "TELEGRAM_BOT_TOKEN": "12345:abc"}
    assert should_autostart(mode="auto", config=config, env_values=env) is True


def test_should_autostart_auto_picks_up_env_var_provider_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider key in os.environ counts, not just .env (CONTRACT.md §2.1 Option C)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-railway")
    config = {"model": {"provider": "anthropic"}}
    env = {"TELEGRAM_BOT_TOKEN": "12345:abc"}
    assert should_autostart(mode="auto", config=config, env_values=env) is True


def test_should_autostart_unknown_provider_returns_false() -> None:
    config = {"model": {"provider": "made-up-provider"}}
    assert should_autostart(mode="auto", config=config, env_values={"ANYTHING": "x"}) is False


# ─────────────────────────────────────────────────────── proxy filters


def test_filter_request_headers_drops_hop_by_hop_and_host() -> None:
    headers = {
        "Host": "downstream.example",
        "Connection": "keep-alive",
        "Transfer-Encoding": "chunked",
        "Upgrade": "websocket",
        "X-Real-IP": "10.0.0.1",
        "User-Agent": "smoke",
        "Cookie": "a=1; b=2",
    }
    out = _filter_request_headers(headers)
    assert "Host" not in out
    assert "Connection" not in out
    assert "Transfer-Encoding" not in out
    assert "Upgrade" not in out
    assert out["X-Real-IP"] == "10.0.0.1"
    assert out["User-Agent"] == "smoke"
    assert out["Cookie"] == "a=1; b=2"


def test_strip_our_cookies_removes_only_admin_session() -> None:
    cookie = "hermes_station_admin=signed-blob; sessionid=webui-blob; theme=dark"
    out = _strip_our_cookies(cookie)
    assert "hermes_station_admin" not in out
    assert "sessionid=webui-blob" in out
    assert "theme=dark" in out


def test_strip_our_cookies_passes_through_other_cookies() -> None:
    cookie = "sessionid=webui-blob; csrf=xyz"
    out = _strip_our_cookies(cookie)
    assert out == "sessionid=webui-blob; csrf=xyz"


def test_strip_our_cookies_handles_empty() -> None:
    assert _strip_our_cookies(None) == ""
    assert _strip_our_cookies("") == ""


# ────────────────────────────────────────────────── supervisor state defaults


def test_webui_process_is_not_running_when_constructed(tmp_path: Path) -> None:
    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    assert proc.is_running() is False


def test_gateway_state_unknown_when_no_state_file(tmp_path: Path) -> None:
    gw = Gateway(hermes_home=tmp_path)
    assert gw.is_running() is False
    assert gw.gateway_state == "unknown"
    assert gw.is_healthy() is False


def test_gateway_state_reads_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "gateway_state.json"
    state_file.write_text('{"gateway_state": "running", "pid": 12345}')
    gw = Gateway(hermes_home=tmp_path)
    assert gw.gateway_state == "running"
    # is_healthy still False because task isn't set in this unit test
    assert gw.is_healthy() is False


def test_gateway_handles_malformed_state_file(tmp_path: Path) -> None:
    (tmp_path / "gateway_state.json").write_text("not json")
    gw = Gateway(hermes_home=tmp_path)
    assert gw.gateway_state == "unknown"
