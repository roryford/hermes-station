"""Tests for hermes_station/gateway.py — pure-logic paths only.

We deliberately avoid testing _run_once / _supervise / start / stop because
those require the 'gateway' package (hermes-agent extra) which is not
installed in the test environment.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes_station.gateway import Gateway, should_autostart


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_gateway(tmp_path: Path) -> Gateway:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    return Gateway(hermes_home=hermes_home)


# ---------------------------------------------------------------------------
# Gateway.read_state()
# ---------------------------------------------------------------------------


class TestReadState:
    def test_missing_file_returns_unknown(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        assert gw.read_state() == {"gateway_state": "unknown"}

    def test_bad_json_returns_unknown(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        gw.state_path.write_text("not valid json", encoding="utf-8")
        assert gw.read_state() == {"gateway_state": "unknown"}

    def test_valid_json_returned(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        data = {"gateway_state": "running", "updated_at": "2025-01-01T00:00:00+00:00"}
        gw.state_path.write_text(json.dumps(data), encoding="utf-8")
        assert gw.read_state() == data

    def test_empty_file_returns_unknown(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        gw.state_path.write_text("", encoding="utf-8")
        assert gw.read_state() == {"gateway_state": "unknown"}


# ---------------------------------------------------------------------------
# Gateway.gateway_state property
# ---------------------------------------------------------------------------


class TestGatewayStateProperty:
    def test_delegates_to_read_state(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        gw.state_path.write_text(json.dumps({"gateway_state": "stopped"}), encoding="utf-8")
        assert gw.gateway_state == "stopped"

    def test_missing_key_returns_unknown(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        gw.state_path.write_text(json.dumps({"other": "field"}), encoding="utf-8")
        assert gw.gateway_state == "unknown"

    def test_no_file_returns_unknown(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        assert gw.gateway_state == "unknown"


# ---------------------------------------------------------------------------
# Gateway.is_running() / is_healthy()
# ---------------------------------------------------------------------------


class TestPredicates:
    def test_no_task_not_running(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        assert gw.is_running() is False

    def test_done_task_not_running(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        gw.task = task
        assert gw.is_running() is False

    def test_running_task_is_running(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        gw.task = task
        assert gw.is_running() is True

    def test_not_running_not_healthy(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        assert gw.is_healthy() is False

    def test_running_but_state_not_running_not_healthy(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        gw.task = task
        gw.state_path.write_text(json.dumps({"gateway_state": "starting"}), encoding="utf-8")
        assert gw.is_healthy() is False

    def test_running_task_and_state_is_healthy(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        gw.task = task
        gw.state_path.write_text(json.dumps({"gateway_state": "running"}), encoding="utf-8")
        assert gw.is_healthy() is True


# ---------------------------------------------------------------------------
# Gateway.snapshot()
# ---------------------------------------------------------------------------


def _write_state(gw: Gateway, data: dict) -> None:
    gw.state_path.write_text(json.dumps(data), encoding="utf-8")


def _fresh_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()


def _stale_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()


class TestSnapshot:
    def test_connected_when_running_and_fresh_updated_at(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": _fresh_ts()})
        snap = gw.snapshot()
        assert snap["connection"] == "connected"
        assert snap["state"] == "running"

    def test_disconnected_when_running_and_stale_updated_at(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": _stale_ts()})
        snap = gw.snapshot()
        assert snap["connection"] == "disconnected"

    def test_disconnected_when_running_and_no_updated_at(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running"})
        snap = gw.snapshot()
        assert snap["connection"] == "disconnected"

    def test_disconnected_when_running_and_invalid_updated_at(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": "not-a-date"})
        snap = gw.snapshot()
        assert snap["connection"] == "disconnected"

    def test_not_configured_when_state_unknown(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "unknown"})
        snap = gw.snapshot()
        assert snap["connection"] == "not_configured"

    def test_not_configured_when_state_stopped(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "stopped"})
        snap = gw.snapshot()
        assert snap["connection"] == "not_configured"

    def test_unknown_connection_for_other_states(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "starting"})
        snap = gw.snapshot()
        assert snap["connection"] == "unknown"

    def test_token_invalid_when_error_contains_token(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(
            gw,
            {"gateway_state": "running", "last_error": "token expired", "updated_at": _fresh_ts()},
        )
        snap = gw.snapshot()
        assert snap["connection"] == "token_invalid"

    def test_token_invalid_when_error_contains_401(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(
            gw,
            {"gateway_state": "running", "error": "HTTP 401 Unauthorized", "updated_at": _fresh_ts()},
        )
        snap = gw.snapshot()
        assert snap["connection"] == "token_invalid"

    def test_token_invalid_when_error_contains_auth(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(
            gw,
            {
                "gateway_state": "stopped",
                "status_detail": "auth failure",
            },
        )
        snap = gw.snapshot()
        assert snap["connection"] == "token_invalid"

    def test_token_invalid_when_error_contains_credential(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(
            gw,
            {"gateway_state": "stopped", "last_error": "invalid credential"},
        )
        snap = gw.snapshot()
        assert snap["connection"] == "token_invalid"

    def test_platform_from_platform_key(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "platform": "telegram", "updated_at": _fresh_ts()})
        snap = gw.snapshot()
        assert snap["platform"] == "telegram"

    def test_platform_from_active_platform_key(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(
            gw,
            {"gateway_state": "running", "active_platform": "discord", "updated_at": _fresh_ts()},
        )
        snap = gw.snapshot()
        assert snap["platform"] == "discord"

    def test_platform_from_primary_platform_key(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(
            gw,
            {
                "gateway_state": "running",
                "primary_platform": "slack",
                "updated_at": _fresh_ts(),
            },
        )
        snap = gw.snapshot()
        assert snap["platform"] == "slack"

    def test_platform_none_when_absent(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": _fresh_ts()})
        snap = gw.snapshot()
        assert snap["platform"] is None

    def test_platform_none_when_whitespace_only(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "platform": "   ", "updated_at": _fresh_ts()})
        snap = gw.snapshot()
        assert snap["platform"] is None

    def test_platform_prefers_platform_over_active_platform(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(
            gw,
            {
                "gateway_state": "running",
                "platform": "telegram",
                "active_platform": "discord",
                "updated_at": _fresh_ts(),
            },
        )
        snap = gw.snapshot()
        assert snap["platform"] == "telegram"

    def test_passthrough_last_auth_failure_at(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        ts = _fresh_ts()
        _write_state(
            gw,
            {
                "gateway_state": "running",
                "updated_at": _fresh_ts(),
                "last_auth_failure_at": ts,
            },
        )
        snap = gw.snapshot()
        assert snap["last_auth_failure_at"] == ts

    def test_passthrough_last_crash_at(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        ts = _fresh_ts()
        _write_state(
            gw,
            {
                "gateway_state": "running",
                "updated_at": _fresh_ts(),
                "last_crash_at": ts,
            },
        )
        snap = gw.snapshot()
        assert snap["last_crash_at"] == ts

    def test_passthrough_last_error_at(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        ts = _fresh_ts()
        _write_state(
            gw,
            {
                "gateway_state": "running",
                "updated_at": _fresh_ts(),
                "last_error_at": ts,
            },
        )
        snap = gw.snapshot()
        assert snap["last_error_at"] == ts

    def test_absent_signal_keys_omitted(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": _fresh_ts()})
        snap = gw.snapshot()
        assert "last_auth_failure_at" not in snap
        assert "last_crash_at" not in snap
        assert "last_error_at" not in snap

    def test_is_running_and_is_healthy_included(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": _fresh_ts()})
        snap = gw.snapshot()
        assert "is_running" in snap
        assert "is_healthy" in snap

    def test_no_state_file_snapshot(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        snap = gw.snapshot()
        assert snap["state"] == "unknown"
        assert snap["connection"] == "not_configured"
        assert snap["platform"] is None

    def test_updated_at_naive_datetime_treated_as_utc(self, tmp_path: Path) -> None:
        """A naive ISO timestamp (no tzinfo) should be treated as UTC."""
        naive_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).replace(tzinfo=None).isoformat()
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": naive_ts})
        snap = gw.snapshot()
        assert snap["connection"] == "connected"


# ---------------------------------------------------------------------------
# should_autostart() — missing branches
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "COPILOT_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


class TestShouldAutostart:
    def test_force_on_mode_1(self) -> None:
        assert should_autostart(mode="1", config={}, env_values={}) is True

    def test_force_on_mode_true(self) -> None:
        assert should_autostart(mode="true", config={}, env_values={}) is True

    def test_force_on_mode_on(self) -> None:
        assert should_autostart(mode="on", config={}, env_values={}) is True

    def test_force_on_mode_yes(self) -> None:
        assert should_autostart(mode="yes", config={}, env_values={}) is True

    def test_force_off_mode_0(self) -> None:
        config = {"model": {"provider": "anthropic"}}
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        assert should_autostart(mode="0", config=config, env_values=env) is False

    def test_force_off_mode_false(self) -> None:
        config = {"model": {"provider": "anthropic"}}
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        assert should_autostart(mode="false", config=config, env_values=env) is False

    def test_force_off_mode_off(self) -> None:
        config = {"model": {"provider": "anthropic"}}
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        assert should_autostart(mode="off", config=config, env_values=env) is False

    def test_force_off_mode_no(self) -> None:
        config = {"model": {"provider": "anthropic"}}
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        assert should_autostart(mode="no", config=config, env_values=env) is False

    def test_auto_no_provider(self) -> None:
        assert should_autostart(mode="auto", config={}, env_values={}) is False

    def test_auto_unknown_provider(self) -> None:
        config = {"model": {"provider": "nonexistent-llm"}}
        assert should_autostart(mode="auto", config=config, env_values={}) is False

    def test_auto_provider_missing_key(self) -> None:
        config = {"model": {"provider": "anthropic"}}
        assert should_autostart(mode="auto", config=config, env_values={}) is False

    def test_auto_provider_with_key(self) -> None:
        config = {"model": {"provider": "anthropic"}}
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        assert should_autostart(mode="auto", config=config, env_values=env) is True

    def test_auto_defaults_to_auto_when_empty_mode(self) -> None:
        """Empty / None mode defaults to 'auto'."""
        assert should_autostart(mode="", config={}, env_values={}) is False

    def test_force_on_case_insensitive(self) -> None:
        assert should_autostart(mode="TRUE", config={}, env_values={}) is True
        assert should_autostart(mode="YES", config={}, env_values={}) is True

    def test_force_off_case_insensitive(self) -> None:
        assert should_autostart(mode="FALSE", config={}, env_values={}) is False
        assert should_autostart(mode="OFF", config={}, env_values={}) is False


# ---------------------------------------------------------------------------
# _refresh_updated_at() coroutine
# ---------------------------------------------------------------------------


class TestRefreshUpdatedAt:
    @pytest.mark.asyncio
    async def test_patches_updated_at_when_running(self, tmp_path: Path) -> None:
        """Drive one iteration: write state=running, let the coroutine tick once."""
        gw = make_gateway(tmp_path)
        original_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _write_state(gw, {"gateway_state": "running", "updated_at": original_ts})

        # Override HEARTBEAT_INTERVAL_SECONDS to near-zero so the sleep is fast.
        gw.HEARTBEAT_INTERVAL_SECONDS = 0.01

        async def _run_once_then_stop() -> None:
            # Give the coroutine one tick, then stop it.
            await asyncio.sleep(0.05)
            gw._stopping.set()

        await asyncio.gather(
            gw._refresh_updated_at(),
            _run_once_then_stop(),
        )

        result = gw.read_state()
        assert result["gateway_state"] == "running"
        new_ts = datetime.fromisoformat(result["updated_at"])
        old_ts = datetime.fromisoformat(original_ts)
        assert new_ts > old_ts

    @pytest.mark.asyncio
    async def test_skips_patch_when_state_not_running(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "stopped"})
        gw.HEARTBEAT_INTERVAL_SECONDS = 0.01

        async def _stop_soon() -> None:
            await asyncio.sleep(0.05)
            gw._stopping.set()

        await asyncio.gather(gw._refresh_updated_at(), _stop_soon())

        result = gw.read_state()
        # updated_at should NOT have been written
        assert "updated_at" not in result

    @pytest.mark.asyncio
    async def test_stops_immediately_when_stopping_set(self, tmp_path: Path) -> None:
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": _fresh_ts()})
        gw._stopping.set()
        # Should return quickly without doing anything
        await asyncio.wait_for(gw._refresh_updated_at(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_returns_on_cancelled_error(self, tmp_path: Path) -> None:
        """CancelledError during sleep causes clean return (no re-raise)."""
        gw = make_gateway(tmp_path)
        _write_state(gw, {"gateway_state": "running", "updated_at": _fresh_ts()})
        gw.HEARTBEAT_INTERVAL_SECONDS = 60.0  # long sleep so cancel fires first

        task = asyncio.create_task(gw._refresh_updated_at())
        await asyncio.sleep(0.01)
        task.cancel()
        # _refresh_updated_at suppresses CancelledError and returns cleanly.
        await task  # should not raise
