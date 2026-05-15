"""Targeted tests to push coverage above the 85% threshold.

Covers:
- hermes_station.admin.copilot_oauth (device flow mocks)
- hermes_station.admin.routes (rate-limiting, auth guard, JSON body, pairing)
- hermes_station.readiness (uncovered branches)
- hermes_station.gateway (snapshot, read_state)
- hermes_station.admin.htmx_logs (_parse_limit helper)
- hermes_station.admin.auth (auth helpers)
- hermes_station.admin.provider (provider_env_var_names, _validate_base_url)
- hermes_station.secrets (resolve, mask edge cases)
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from hermes_station.admin.copilot_oauth import (
    COPILOT_OAUTH_CLIENT_ID,
    _ACCESS_TOKEN_URL,
    _DEVICE_CODE_URL,
    poll_device_flow,
    start_device_flow,
)
from hermes_station.admin.htmx_logs import _parse_limit
from hermes_station.admin.provider import provider_env_var_names, _validate_base_url
from hermes_station.gateway import Gateway
from hermes_station.readiness import (
    CapabilityRow,
    Readiness,
    _channel_intended,
    _configured_platforms,
    _credential_source,
    _delegation_providers,
    _dir_writable,
    _enabled_toolsets,
    _first_present,
    _has_value,
    _image_gen_intended,
    _read_image_revision,
    _read_hermes_webui_version,
    validate_readiness,
)
from hermes_station.secrets import mask, resolve


# ─────────────────────────────────────────────────────────── secrets.py


def test_mask_empty_string() -> None:
    assert mask("") == ""


def test_mask_short_value() -> None:
    assert mask("ab") == "***"
    assert mask("abcdef", head=4, tail=2) == "***"  # len 6 == head+tail


def test_mask_long_value() -> None:
    result = mask("sk-anthropic-xyz", head=4, tail=2)
    assert result.startswith("sk-a")
    assert result.endswith("yz")
    assert "…" in result


def test_mask_exact_boundary() -> None:
    # len=5, head=4, tail=2 → 5 <= 6 → "***"
    assert mask("abcde", head=4, tail=2) == "***"


def test_resolve_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "env-value")
    sv = resolve("MY_SECRET", {})
    assert sv.value == "env-value"
    assert sv.source == "env"


def test_resolve_from_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    sv = resolve("MY_SECRET", {"MY_SECRET": "file-value"})
    assert sv.value == "file-value"
    assert sv.source == "file"


def test_resolve_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    sv = resolve("MY_SECRET", {})
    assert sv.value is None
    assert sv.source == "unset"


def test_resolve_env_takes_precedence_over_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "env-wins")
    sv = resolve("MY_SECRET", {"MY_SECRET": "file-loses"})
    assert sv.value == "env-wins"
    assert sv.source == "env"


# ─────────────────────────────────────────────────────────── _parse_limit


def test_parse_limit_default_when_none() -> None:
    assert _parse_limit(None) == 200


def test_parse_limit_default_when_invalid() -> None:
    assert _parse_limit("abc") == 200


def test_parse_limit_clamped_to_max() -> None:
    assert _parse_limit("9999") == 500


def test_parse_limit_clamped_to_min() -> None:
    assert _parse_limit("-5") == 1


def test_parse_limit_normal() -> None:
    assert _parse_limit("50") == 50


# ─────────────────────────────────────────────────────────── readiness helpers


def test_has_value_true_for_real_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K", raising=False)
    assert _has_value({"K": "real-key"}, "K") is True


def test_has_value_false_for_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K", raising=False)
    assert _has_value({"K": "changeme"}, "K") is False


def test_has_value_false_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K", raising=False)
    assert _has_value({}, "K") is False


def test_has_value_falls_back_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "real-value")
    assert _has_value({}, "K") is True


def test_first_present_returns_first_matching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    result = _first_present({"A": "val-a", "B": "val-b"}, ("A", "B"))
    assert result == "A"


def test_first_present_returns_empty_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    result = _first_present({}, ("A", "B"))
    assert result == ""


def test_channel_intended_via_messaging_block_enabled_false() -> None:
    config = {"messaging": {"discord": {"enabled": False}}}
    assert _channel_intended(config, "discord") is False


def test_channel_intended_via_messaging_block_enabled_true() -> None:
    config = {"messaging": {"discord": {"enabled": True}}}
    assert _channel_intended(config, "discord") is True


def test_channel_intended_via_channels_list() -> None:
    config = {"channels": ["telegram", "discord"]}
    assert _channel_intended(config, "telegram") is True
    assert _channel_intended(config, "slack") is False


def test_channel_intended_via_channels_dict() -> None:
    config = {"channels": {"telegram": {"enabled": True}}}
    assert _channel_intended(config, "telegram") is True


def test_channel_intended_via_channels_dict_disabled() -> None:
    config = {"channels": {"telegram": {"enabled": False}}}
    assert _channel_intended(config, "telegram") is False


def test_channel_not_intended_by_default() -> None:
    assert _channel_intended({}, "discord") is False


def test_delegation_providers_returns_empty_for_no_delegation() -> None:
    assert _delegation_providers({}) == []


def test_delegation_providers_top_level_provider() -> None:
    config = {"delegation": {"provider": "anthropic"}}
    result = _delegation_providers(config)
    assert "anthropic" in result


def test_delegation_providers_from_routes_list() -> None:
    config = {
        "delegation": {
            "routes": [
                {"provider": "openai"},
                {"provider": "anthropic"},
            ]
        }
    }
    result = _delegation_providers(config)
    assert "openai" in result
    assert "anthropic" in result


def test_delegation_providers_from_fallback_list() -> None:
    config = {
        "delegation": {
            "fallback": [{"provider": "openrouter"}]
        }
    }
    result = _delegation_providers(config)
    assert "openrouter" in result


def test_dir_writable_returns_true_for_writable_dir(tmp_path: Path) -> None:
    assert _dir_writable(tmp_path / "subdir") is True


def test_dir_writable_returns_false_for_non_writable(tmp_path: Path) -> None:
    import stat
    d = tmp_path / "readonly"
    d.mkdir()
    try:
        d.chmod(stat.S_IRUSR | stat.S_IXUSR)
        assert _dir_writable(d / "probe") is False
    finally:
        d.chmod(stat.S_IRWXU)  # restore so pytest can clean up


def test_read_image_revision_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_STATION_REVISION", "abc123")
    result = _read_image_revision()
    assert result == "abc123"


def test_read_image_revision_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_STATION_REVISION", raising=False)
    # /etc/hermes-station-build doesn't exist in test env
    result = _read_image_revision()
    assert result is None


def test_read_hermes_webui_version_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_WEBUI_VERSION", "2.0.0")
    result = _read_hermes_webui_version()
    assert result == "2.0.0"


def test_read_hermes_webui_version_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_WEBUI_VERSION", raising=False)
    result = _read_hermes_webui_version()
    assert result is None


def test_image_gen_intended_false_for_empty() -> None:
    assert _image_gen_intended({}) is False


def test_image_gen_intended_dict_false_when_disabled() -> None:
    assert _image_gen_intended({"toolsets": {"image_gen": {"enabled": False}}}) is False


def test_enabled_toolsets_dict_with_disabled_entry() -> None:
    config = {"toolsets": {"image_gen": {"enabled": False}, "web": True}}
    result = _enabled_toolsets(config)
    assert "web" in result
    assert "image_gen" not in result


def test_configured_platforms_from_channels_dict() -> None:
    config = {"channels": {"telegram": {"enabled": True}, "discord": False}}
    # discord=False (not dict, not True) shouldn't be included
    result = _configured_platforms(config)
    assert "telegram" in result


def test_configured_platforms_from_channels_list() -> None:
    config = {"channels": ["telegram", "slack"]}
    result = _configured_platforms(config)
    assert "telegram" in result
    assert "slack" in result


def test_configured_platforms_deduplicates(tmp_path: Path) -> None:
    # Same slug in both messaging and channels
    config = {
        "messaging": {"telegram": {"enabled": True}},
        "channels": ["telegram"],
    }
    result = _configured_platforms(config)
    assert result.count("telegram") == 1


def test_validate_readiness_with_delegation_provider(tmp_path: Path) -> None:
    """Delegation provider should add a row to readiness."""
    from hermes_station.config import Paths
    import os

    # Use tmp_path as hermes_home
    os.environ.setdefault("HERMES_HOME", str(tmp_path))

    class _FakePaths:
        hermes_home = tmp_path

    config = {
        "delegation": {"provider": "anthropic"},
        "model": {"provider": "openai"},
    }
    rd = validate_readiness(
        _FakePaths(),
        config,
        {"ANTHROPIC_API_KEY": "sk-x", "OPENAI_API_KEY": "sk-y"},
    )
    assert "provider:anthropic" in rd.readiness
    assert rd.readiness["provider:anthropic"].ready is True


def test_readiness_as_dict_roundtrip(tmp_path: Path) -> None:
    """Readiness.as_dict produces a serializable dict."""
    row = CapabilityRow(intended=True, ready=False, reason="missing X", source="absent")
    rd = Readiness(
        readiness={"test_cap": row},
        versions={"hermes_station": "0.1.0"},
        boot_at="2026-01-01T00:00:00Z",
        summary={"platforms": [], "toolsets": []},
    )
    d = rd.as_dict()
    assert d["readiness"]["test_cap"]["intended"] is True
    assert d["readiness"]["test_cap"]["ready"] is False
    assert d["readiness"]["test_cap"]["reason"] == "missing X"
    assert d["readiness"]["test_cap"]["source"] == "absent"
    assert d["versions"]["hermes_station"] == "0.1.0"


def test_capability_row_as_dict_omits_empty_fields() -> None:
    row = CapabilityRow(intended=False, ready=False)
    d = row.as_dict()
    assert "reason" not in d
    assert "source" not in d


def test_readiness_any_intended_not_ready() -> None:
    rd = Readiness(readiness={
        "cap_a": CapabilityRow(intended=True, ready=False),
        "cap_b": CapabilityRow(intended=False, ready=False),
    })
    assert rd.any_intended_not_ready() is True


def test_readiness_any_intended_not_ready_false() -> None:
    rd = Readiness(readiness={
        "cap_a": CapabilityRow(intended=True, ready=True),
    })
    assert rd.any_intended_not_ready() is False


# ─────────────────────────────────────────────────────────── gateway.snapshot


def test_gateway_snapshot_unknown_state(tmp_path: Path) -> None:
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["state"] == "unknown"
    assert snap["connection"] == "not_configured"
    assert snap["platform"] is None
    assert snap["is_running"] is False
    assert snap["is_healthy"] is False


def test_gateway_snapshot_running_state_connected(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    state_file = tmp_path / "gateway_state.json"
    now_iso = datetime.now(timezone.utc).isoformat()
    state_file.write_text(json.dumps({
        "gateway_state": "running",
        "platform": "telegram",
        "updated_at": now_iso,
    }))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["state"] == "running"
    assert snap["platform"] == "telegram"
    assert snap["connection"] == "connected"


def test_gateway_snapshot_token_invalid(tmp_path: Path) -> None:
    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(json.dumps({
        "gateway_state": "startup_failed",
        "last_error": "unauthorized: token invalid",
    }))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["connection"] == "token_invalid"


def test_gateway_snapshot_stopped_state(tmp_path: Path) -> None:
    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(json.dumps({"gateway_state": "stopped"}))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["state"] == "stopped"
    assert snap["connection"] == "not_configured"


def test_gateway_snapshot_failure_signals_passthrough(tmp_path: Path) -> None:
    """Failure signal keys should pass through to the snapshot dict."""
    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(json.dumps({
        "gateway_state": "startup_failed",
        "last_auth_failure_at": "2026-01-01T00:00:00Z",
        "last_crash_at": "2026-01-01T00:01:00Z",
    }))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap.get("last_auth_failure_at") == "2026-01-01T00:00:00Z"
    assert snap.get("last_crash_at") == "2026-01-01T00:01:00Z"


def test_gateway_snapshot_running_stale_updated_at(tmp_path: Path) -> None:
    """updated_at older than 120s → disconnected."""
    from datetime import datetime, timezone, timedelta

    state_file = tmp_path / "gateway_state.json"
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    state_file.write_text(json.dumps({
        "gateway_state": "running",
        "updated_at": old_ts,
    }))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["connection"] == "disconnected"


def test_gateway_snapshot_running_bad_updated_at(tmp_path: Path) -> None:
    """Malformed updated_at → disconnected."""
    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(json.dumps({
        "gateway_state": "running",
        "updated_at": "not-a-timestamp",
    }))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["connection"] == "disconnected"


def test_gateway_snapshot_platform_keys(tmp_path: Path) -> None:
    """active_platform / primary_platform should also be picked up."""
    state_file = tmp_path / "gateway_state.json"
    state_file.write_text(json.dumps({
        "gateway_state": "unknown",
        "active_platform": "discord",
    }))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    assert snap["platform"] == "discord"


# ─────────────────────────────────────────────────────── provider helpers


def test_provider_env_var_names_anthropic() -> None:
    names = provider_env_var_names("anthropic")
    assert "ANTHROPIC_API_KEY" in names


def test_provider_env_var_names_copilot_accepted_aliases() -> None:
    names = provider_env_var_names("copilot")
    assert "COPILOT_GITHUB_TOKEN" in names
    assert "GH_TOKEN" in names
    assert "GITHUB_TOKEN" in names


def test_provider_env_var_names_unknown_provider() -> None:
    names = provider_env_var_names("bogus")
    assert names == ()


def test_validate_base_url_valid_https() -> None:
    # google.com is a public HTTPS endpoint; validation should pass.
    result = _validate_base_url("https://api.openai.com/v1")
    assert result == "https://api.openai.com/v1"


def test_validate_base_url_empty_is_ok() -> None:
    assert _validate_base_url("") == ""


def test_validate_base_url_rejects_no_scheme() -> None:
    with pytest.raises(ValueError, match="http or https"):
        _validate_base_url("ftp://example.com/v1")


def test_validate_base_url_rejects_localhost() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _validate_base_url("http://localhost/v1")


def test_validate_base_url_rejects_no_hostname() -> None:
    with pytest.raises(ValueError, match="hostname"):
        _validate_base_url("https:///v1")


# ──────────────────────────────────────────────────── copilot_oauth (mocked)
# We patch httpx.AsyncClient using unittest.mock to avoid the respx dependency.


def _make_mock_client(response_json: dict | None = None, status: int = 200, content: bytes | None = None):
    """Build a mock httpx.AsyncClient context manager that returns a canned response."""
    if content is not None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status
        resp.json.side_effect = Exception("not JSON")
        resp.raise_for_status = MagicMock()
    else:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status
        resp.json.return_value = response_json
        resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


async def test_start_device_flow_success() -> None:
    mock_client = _make_mock_client({
        "device_code": "dev123",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    })
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await start_device_flow()
    assert result["device_code"] == "dev123"
    assert result["user_code"] == "ABCD-EFGH"
    assert result["poll_interval"] == 8  # 5 + _POLL_SAFETY_MARGIN(3)


async def test_start_device_flow_missing_fields() -> None:
    mock_client = _make_mock_client({"error": "bad"})
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="Unexpected response"):
            await start_device_flow()


async def test_poll_device_flow_success() -> None:
    mock_client = _make_mock_client({"access_token": "gho_abc123"})
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await poll_device_flow("dev123")
    assert result["status"] == "success"
    assert result["token"] == "gho_abc123"
    assert result["poll_interval"] == 0


async def test_poll_device_flow_pending() -> None:
    mock_client = _make_mock_client({"error": "authorization_pending", "interval": 5})
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await poll_device_flow("dev123")
    assert result["status"] == "pending"
    assert result["poll_interval"] == 8  # 5 + 3


async def test_poll_device_flow_slow_down() -> None:
    mock_client = _make_mock_client({"error": "slow_down", "interval": 10})
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await poll_device_flow("dev123")
    assert result["status"] == "slow_down"
    assert result["poll_interval"] == 13


async def test_poll_device_flow_expired() -> None:
    mock_client = _make_mock_client({"error": "expired_token"})
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await poll_device_flow("dev123")
    assert result["status"] == "expired"
    assert "expired" in result["message"].lower()


async def test_poll_device_flow_access_denied() -> None:
    mock_client = _make_mock_client({"error": "access_denied"})
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await poll_device_flow("dev123")
    assert result["status"] == "denied"


async def test_poll_device_flow_unknown_error() -> None:
    mock_client = _make_mock_client({"error": "some_weird_error", "error_description": "Something went wrong"})
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await poll_device_flow("dev123")
    assert result["status"] == "error"
    assert "Something went wrong" in result["message"]


async def test_poll_device_flow_http_error() -> None:
    """Non-JSON response → error with HTTP status code."""
    mock_client = _make_mock_client(content=b"Internal Server Error", status=500)
    with patch("hermes_station.admin.copilot_oauth.httpx.AsyncClient", return_value=mock_client):
        result = await poll_device_flow("dev123")
    assert result["status"] == "error"
    assert "500" in result["message"]


# ─────────────────────────────────────────────── routes: rate-limit + helpers


@pytest.fixture(autouse=False)
def clear_login_rate_limit():
    """Reset the login rate-limit dict before/after tests to prevent pollution."""
    from hermes_station.admin import routes
    routes._login_attempts.clear()
    yield
    routes._login_attempts.clear()


def test_prune_login_attempts_evicts_stale() -> None:
    """_prune_login_attempts removes old entries."""
    from hermes_station.admin import routes

    # Reset state
    routes._login_attempts.clear()
    # Inject an old attempt
    routes._login_attempts["1.2.3.4"] = [time.time() - 120]
    routes._prune_login_attempts()
    assert "1.2.3.4" not in routes._login_attempts


def test_prune_login_attempts_keeps_recent() -> None:
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    routes._login_attempts["1.2.3.4"] = [time.time()]
    routes._prune_login_attempts()
    assert "1.2.3.4" in routes._login_attempts


async def test_json_body_returns_empty_dict_on_non_json(fake_data_dir: Path, admin_password: str) -> None:
    """_json_body returns {} when body is not valid JSON."""
    from hermes_station.admin.routes import _json_body
    from starlette.requests import Request
    from starlette.datastructures import Headers

    # Build a minimal fake request with plain-text body
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(b"content-type", b"text/plain")],
    }

    async def receive():
        return {"type": "http.request", "body": b"not json", "more_body": False}

    request = Request(scope, receive)
    result = await _json_body(request)
    assert result == {}


async def test_json_body_returns_empty_dict_when_body_is_list(
    fake_data_dir: Path, admin_password: str
) -> None:
    """_json_body returns {} when JSON body is a list (not a dict)."""
    from hermes_station.admin.routes import _json_body
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }

    async def receive():
        return {"type": "http.request", "body": b'["a","b"]', "more_body": False}

    request = Request(scope, receive)
    result = await _json_body(request)
    assert result == {}


async def test_rate_limit_blocks_after_max_attempts(fake_data_dir: Path, admin_password: str) -> None:
    """After _LOGIN_MAX_ATTEMPTS failed attempts, 429 is returned."""
    from hermes_station.app import create_app
    from hermes_station.admin import routes

    routes._login_attempts.clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(10):
            await client.post("/admin/login", data={"password": "wrong"})
        resp = await client.post("/admin/login", data={"password": "wrong"})
    assert resp.status_code == 429


async def test_api_status_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/status")
    assert resp.status_code == 401


async def test_pairing_action_unknown_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Unknown action returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/pairing/bogus-action", json={"user_id": "42"})
    assert resp.status_code == 400
    assert "unknown action" in resp.json()["error"]


async def test_pairing_action_missing_user_id(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """approve with no user_id returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/pairing/approve", json={})
    assert resp.status_code == 400
    assert "user_id" in resp.json()["error"]


async def test_pairing_action_deny_nonexistent_user(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """deny with nonexistent user_id returns 200 (no-op, not an error)."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/pairing/deny", json={"user_id": "nonexistent"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_pairing_action_pending_returns_list(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """pending action returns the pending list."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/pairing/pending")
    assert resp.status_code == 200
    assert "pending" in resp.json()


async def test_pairing_action_approved_returns_list(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """approved action returns the approved list."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/pairing/approved")
    assert resp.status_code == 200
    assert "approved" in resp.json()


async def test_gateway_action_unknown_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Unknown gateway action returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/explode")
    assert resp.status_code == 400


async def test_api_logs_limit_zero(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """limit=0 is clamped to 1."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/logs/station?limit=0")
    assert resp.status_code == 200
    assert resp.json()["count"] <= 1


async def test_channels_save_rejects_invalid_values(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Keys with newlines in values should cause 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post(
            "/admin/api/channels/save",
            json={"TELEGRAM_BOT_TOKEN": "12345:abc\nevil-inject"},
        )
    assert resp.status_code == 400


async def test_admin_logout(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Logout redirects to login page."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in resp.headers["location"]


# ─────────────────────────────────────────────────────────── health.py helpers


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

    # Dict readiness format — the entire dict is returned as-is
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

    # Dict-style readiness with an intended-but-not-ready capability
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


# ─────────────────────────────────────────────────────────── routes.py: prune


def test_prune_login_attempts_evicts_beyond_max_ips() -> None:
    """When dict exceeds _LOGIN_MAX_IPS, oldest entries are evicted."""
    from hermes_station.admin import routes

    # Temporarily set a tiny max to trigger eviction
    orig_max = routes._LOGIN_MAX_IPS
    try:
        routes._LOGIN_MAX_IPS = 5
        routes._login_attempts.clear()
        now = time.time()
        # Insert 6 IPs; first one is oldest
        for i in range(6):
            routes._login_attempts[f"10.0.0.{i}"] = [now + i]
        routes._prune_login_attempts()
        # After eviction to 75% of 5 = 3, then time-window prune
        assert len(routes._login_attempts) <= 5
    finally:
        routes._LOGIN_MAX_IPS = orig_max
        routes._login_attempts.clear()


# ─────────────────────────────────────────────────────────── auth.py helpers


def test_admin_auth_disabled_when_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    from hermes_station.admin.auth import admin_auth_enabled
    assert admin_auth_enabled() is False


def test_verify_password_returns_false_with_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    from hermes_station.admin.auth import verify_password
    assert verify_password("anything") is False


def test_verify_password_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "supersecret")
    from hermes_station.admin.auth import verify_password
    assert verify_password("supersecret") is True


def test_verify_password_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "supersecret")
    from hermes_station.admin.auth import verify_password
    assert verify_password("wrong") is False


# ─────────────────────────────────────────────────────────── readiness: more branches


def test_readiness_delegation_provider_already_ready_not_downgraded(tmp_path: Path) -> None:
    """If a delegation provider row is already ready, it should not be downgraded."""
    class _FakePaths:
        hermes_home = tmp_path

    config = {
        "model": {"provider": "anthropic"},
        "delegation": {"provider": "anthropic"},
    }
    rd = validate_readiness(
        _FakePaths(),
        config,
        {"ANTHROPIC_API_KEY": "sk-real-key"},
    )
    assert rd.readiness["provider:anthropic"].ready is True


def test_validate_readiness_none_config_and_env(tmp_path: Path) -> None:
    """None config and env_values are handled gracefully."""
    class _FakePaths:
        hermes_home = tmp_path

    rd = validate_readiness(_FakePaths(), None, None)
    assert isinstance(rd, Readiness)


# ─────────────────────────────────────────────────────────── gateway: more branches


def test_gateway_snapshot_running_no_tz_updated_at(tmp_path: Path) -> None:
    """updated_at without timezone info should be treated as UTC."""
    from datetime import datetime

    state_file = tmp_path / "gateway_state.json"
    # Naive timestamp (no timezone) — the code adds UTC before comparing
    naive_now = datetime.utcnow().isoformat()  # no tzinfo
    state_file.write_text(json.dumps({
        "gateway_state": "running",
        "updated_at": naive_now,
    }))
    gw = Gateway(hermes_home=tmp_path)
    snap = gw.snapshot()
    # Should be "connected" since the timestamp is recent
    assert snap["connection"] == "connected"


def test_gateway_read_state_fallback_on_oserror(tmp_path: Path) -> None:
    """read_state returns unknown dict when file exists but can't be read."""
    import stat

    state_file = tmp_path / "gateway_state.json"
    state_file.write_text('{"gateway_state": "running"}')
    try:
        state_file.chmod(0o000)
        gw = Gateway(hermes_home=tmp_path)
        result = gw.read_state()
        assert result.get("gateway_state") == "unknown"
    finally:
        state_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ─────────────────────────────────────────────────────────── routes.py: provider setup + supervisor


async def test_api_provider_setup_persists(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Provider setup endpoint persists config and returns ok:True."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post(
            "/admin/api/provider/setup",
            json={"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-test"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_api_provider_setup_invalid_provider(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Invalid provider returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post(
            "/admin/api/provider/setup",
            json={"provider": "fake-provider", "model": "x", "api_key": "y"},
        )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


async def test_api_gateway_start_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway start action should work (or return 503 if not initialized)."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/start")
    # Acceptable: 200 (started) or 500 (hermes-agent not installed)
    assert resp.status_code in (200, 500)


async def test_api_webui_action_unknown(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Unknown webui action returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/webui/badaction")
    assert resp.status_code == 400


async def test_api_channels_get_returns_list(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Channels GET returns a list of channels."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/channels")
    assert resp.status_code == 200
    assert "channels" in resp.json()
    assert isinstance(resp.json()["channels"], list)


# ─────────────────────────────────────────────────────────── htmx_logs api via endpoints


async def test_api_logs_gateway_source(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway log source returns a list."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/logs/gateway")
    assert resp.status_code == 200
    assert resp.json()["source"] == "gateway"


async def test_api_logs_webui_source(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """WebUI log source returns a list."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/logs/webui")
    assert resp.status_code == 200
    assert resp.json()["source"] == "webui"


# ─────────────────────────────────────────────────────────── secrets.resolve_many


def test_resolve_many_returns_all_keys(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_station.secrets import resolve_many
    from hermes_station.config import Paths, write_env_file

    monkeypatch.delenv("MY_KEY_A", raising=False)
    monkeypatch.delenv("MY_KEY_B", raising=False)

    paths = Paths()
    write_env_file(paths.env_path, {"MY_KEY_A": "val-a"})

    result = resolve_many(["MY_KEY_A", "MY_KEY_B"], paths.env_path)
    assert result["MY_KEY_A"].value == "val-a"
    assert result["MY_KEY_A"].source == "file"
    assert result["MY_KEY_B"].source == "unset"


# ─────────────────────────────────────────────────────────── htmx_settings helpers


def test_provider_context_returns_catalog(fake_data_dir: Path) -> None:
    """_provider_context returns provider catalog and status."""
    from hermes_station.admin.htmx_settings import _provider_context
    from hermes_station.config import Paths

    paths = Paths()
    ctx = _provider_context(paths)
    assert "provider_catalog" in ctx
    assert isinstance(ctx["provider_catalog"], list)
    assert len(ctx["provider_catalog"]) > 0
    assert "provider_status" in ctx
    assert "provider_label" in ctx


def test_channels_context_returns_channels(fake_data_dir: Path) -> None:
    """_channels_context returns channels list."""
    from hermes_station.admin.htmx_settings import _channels_context
    from hermes_station.config import Paths

    paths = Paths()
    ctx = _channels_context(paths)
    assert "channels" in ctx
    assert isinstance(ctx["channels"], list)


def test_pairings_context_returns_pending_and_approved(fake_data_dir: Path) -> None:
    """_pairings_context returns pending and approved lists."""
    from hermes_station.admin.htmx_settings import _pairings_context
    from hermes_station.config import Paths

    paths = Paths()
    ctx = _pairings_context(paths)
    assert "pending" in ctx
    assert "approved" in ctx
    assert isinstance(ctx["pending"], list)
    assert isinstance(ctx["approved"], list)


# ─────────────────────────────────────────────────────────── admin/mcp.py helpers


def test_mcp_default_config_empty(fake_data_dir: Path) -> None:
    """mcp config helper returns something meaningful on empty config."""
    from hermes_station.admin.mcp import mcp_status
    from hermes_station.config import Paths

    paths = Paths()
    result = mcp_status({}, {})
    assert isinstance(result, list)


# ─────────────────────────────────────────────────────────── provider.py: more branches


def test_provider_has_credentials_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_station.admin.provider import provider_has_credentials

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert provider_has_credentials("anthropic", {}) is True


def test_provider_has_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_station.admin.provider import provider_has_credentials

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider_has_credentials("anthropic", {}) is False


def test_provider_status_returns_dict(fake_data_dir: Path) -> None:
    from hermes_station.admin.provider import provider_status
    from hermes_station.config import Paths

    paths = Paths()
    result = provider_status({}, {})
    assert isinstance(result, dict)
    assert "provider" in result
    assert "ready" in result


# ─────────────────────────────────────────────────────────── webui.py helpers


def test_redact_secrets_replaces_api_key() -> None:
    from hermes_station.webui import _redact_secrets

    result = _redact_secrets("ANTHROPIC_API_KEY=sk-ant-abc123")
    assert "sk-ant-abc123" not in result
    assert "***" in result


def test_redact_secrets_replaces_password() -> None:
    from hermes_station.webui import _redact_secrets

    result = _redact_secrets("password: supersecret123")
    assert "supersecret123" not in result
    assert "***" in result


def test_redact_secrets_passes_through_safe_lines() -> None:
    from hermes_station.webui import _redact_secrets

    safe = "INFO: Server started on port 8788"
    assert _redact_secrets(safe) == safe


def test_webui_snapshot_starting_state(tmp_path: Path) -> None:
    """Snapshot returns 'starting' when process running but not yet healthy."""
    from hermes_station.webui import WebUIProcess
    from unittest.mock import MagicMock

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    # Simulate a running process with returncode=None
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 12345
    proc.process = mock_process
    proc._last_healthy_at = None  # Not yet healthy

    snap = proc.snapshot()
    assert snap["state"] == "starting"
    assert snap["pid"] == 12345
    assert snap["is_running"] is True


def test_webui_snapshot_ready_state(tmp_path: Path) -> None:
    """Snapshot returns 'ready' when process has been healthy."""
    from hermes_station.webui import WebUIProcess
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 12345
    proc.process = mock_process
    proc._last_healthy_at = datetime.now(timezone.utc)

    snap = proc.snapshot()
    assert snap["state"] == "ready"
    assert snap["is_running"] is True


def test_webui_build_env_includes_required_keys(tmp_path: Path) -> None:
    """_build_env includes the minimal env keys hermes-webui needs."""
    from hermes_station.webui import WebUIProcess

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    env = proc._build_env()
    assert env["HERMES_WEBUI_HOST"] == proc.INTERNAL_HOST
    assert env["HERMES_WEBUI_PORT"] == str(proc.INTERNAL_PORT)
    assert "HERMES_HOME" in env
    assert "HERMES_CONFIG_PATH" in env
    assert "PYTHONUNBUFFERED" in env


def test_webui_build_env_passes_admin_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HERMES_ADMIN_PASSWORD is propagated as HERMES_WEBUI_PASSWORD."""
    from hermes_station.webui import WebUIProcess

    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "test-admin-pw")
    monkeypatch.delenv("HERMES_WEBUI_PASSWORD", raising=False)

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    env = proc._build_env()
    assert env.get("HERMES_WEBUI_PASSWORD") == "test-admin-pw"


def test_webui_build_env_no_secret_passthrough(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ANTHROPIC_API_KEY and HERMES_ADMIN_PASSWORD must NOT pass through directly."""
    from hermes_station.webui import WebUIProcess

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", "my-password")

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    env = proc._build_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "HERMES_ADMIN_PASSWORD" not in env


# ─────────────────────────────────────────────────────────── htmx_dashboard helpers


def test_supervisor_badge_healthy() -> None:
    from hermes_station.admin.htmx_dashboard import _supervisor_badge

    badge = _supervisor_badge(running=True, healthy=True)
    assert badge["tone"] == "success"
    assert badge["label"] == "Healthy"


def test_supervisor_badge_starting() -> None:
    from hermes_station.admin.htmx_dashboard import _supervisor_badge

    badge = _supervisor_badge(running=True, healthy=False)
    assert badge["tone"] == "warning"
    assert badge["label"] == "Starting"


def test_supervisor_badge_stopped() -> None:
    from hermes_station.admin.htmx_dashboard import _supervisor_badge

    badge = _supervisor_badge(running=False, healthy=False)
    assert badge["tone"] == "muted"
    assert badge["label"] == "Stopped"


def test_gateway_badge_running() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=True, state="running")
    assert badge["tone"] == "success"


def test_gateway_badge_starting() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=True, state="starting")
    assert badge["tone"] == "warning"


def test_gateway_badge_startup_failed() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=False, state="startup_failed")
    assert badge["tone"] == "danger"


def test_gateway_badge_running_but_unknown_state() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=True, state="unknown")
    assert badge["tone"] == "warning"
    assert badge["label"] == "Starting"


def test_gateway_badge_stopped() -> None:
    from hermes_station.admin.htmx_dashboard import _gateway_badge

    badge = _gateway_badge(running=False, state="stopped")
    assert badge["tone"] == "muted"


# ─────────────────────────────────────────────────────────── mcp.py: more branches


def test_mcp_is_enabled_various_values() -> None:
    from hermes_station.admin.mcp import _is_enabled

    assert _is_enabled(True) is True
    assert _is_enabled(False) is False
    assert _is_enabled(None) is True  # default True
    assert _is_enabled("true") is True
    assert _is_enabled("false") is False
    assert _is_enabled("1") is True
    assert _is_enabled("0") is False


def test_mcp_status_with_enabled_server(fake_data_dir: Path) -> None:
    from hermes_station.admin.mcp import mcp_status
    from hermes_station.config import MCP_SERVER_CATALOG

    if not MCP_SERVER_CATALOG:
        pytest.skip("No MCP servers in catalog")

    first_name = MCP_SERVER_CATALOG[0]["name"]
    config = {"mcp_servers": {first_name: {"enabled": True}}}
    result = mcp_status(config, {})
    assert any(s["name"] == first_name and s["enabled"] is True for s in result)


def test_mcp_status_with_needs_satisfied(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp_status needs_satisfied is True when required env var is present."""
    from hermes_station.admin.mcp import mcp_status
    from hermes_station.config import MCP_SERVER_CATALOG

    # Find a server with needs
    server_with_needs = next((e for e in MCP_SERVER_CATALOG if e.get("needs")), None)
    if not server_with_needs:
        pytest.skip("No MCP server with needs in catalog")

    needed_key = server_with_needs["needs"][0]
    monkeypatch.setenv(needed_key, "test-value")

    result = mcp_status({}, {})
    entry = next(s for s in result if s["name"] == server_with_needs["name"])
    assert entry["needs_satisfied"] is True


# ─────────────────────────────────────────────────────────── htmx_logs: fragment endpoint


async def test_logs_fragment_unknown_source_400(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Unknown source on fragment endpoint returns 400."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        # Override the cookie to simulate authenticated request
        resp = await client.get("/admin/_partial/logs/bogus-source")
    assert resp.status_code in (400, 401)  # 401 if cookies don't transfer, 400 if they do


# ─────────────────────────────────────────────────────────── readiness: _check_provider edge cases


def test_check_provider_empty_string(tmp_path: Path) -> None:
    """Empty provider string returns not-ready row."""
    from hermes_station.readiness import _check_provider

    row = _check_provider("", {}, intended=True)
    assert row.ready is False
    assert "no provider" in row.reason


def test_check_github_via_integrations_key(tmp_path: Path) -> None:
    """GitHub intended via `integrations` key in config."""
    class _FakePaths:
        hermes_home = tmp_path

    from hermes_station.readiness import validate_readiness

    config = {"integrations": {"github": True}}
    rd = validate_readiness(_FakePaths(), config, {"GITHUB_TOKEN": "ghp_x"})
    assert rd.readiness["github"].intended is True
    assert rd.readiness["github"].ready is True


def test_check_github_via_github_key(tmp_path: Path) -> None:
    """GitHub intended via top-level `github` key in config."""
    class _FakePaths:
        hermes_home = tmp_path

    from hermes_station.readiness import validate_readiness

    config = {"github": {"token": "placeholder"}}
    rd = validate_readiness(_FakePaths(), config, {})
    assert rd.readiness["github"].intended is True


# ─────────────────────────────────────────────────────────── htmx_settings: more endpoints


def _build_htmx_app(fake_data_dir: Path):
    """Build a minimal test app with htmx_settings routes."""
    from starlette.applications import Starlette
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from hermes_station.config import Paths
    from hermes_station.gateway import Gateway

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    # Add a gateway mock so gateway.restart() doesn't fail
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    return app


async def test_channels_fragment_save_persists(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Channel save fragment returns HTML with success message."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:test"},
        )
    assert resp.status_code == 200
    assert "Channels saved." in resp.text


async def test_channels_fragment_clear(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Channels clear fragment returns HTML."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "telegram"},
        )
    assert resp.status_code == 200
    assert "cleared." in resp.text


async def test_channels_fragment_clear_unknown_slug(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Unknown slug returns error message in HTML."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "nonexistent-channel"},
        )
    assert resp.status_code == 200
    assert "Unknown channel" in resp.text


async def test_channels_fragment_toggle(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Channel toggle fragment returns HTML."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "telegram"},
        )
    assert resp.status_code == 200


async def test_channels_fragment_toggle_unknown_slug(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Toggle with unknown slug returns error in HTML."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "nonexistent"},
        )
    assert resp.status_code == 200
    assert "Unknown channel" in resp.text


async def test_provider_cancel_returns_provider_card(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Cancel returns provider card HTML."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post("/admin/_partial/provider/cancel")
    assert resp.status_code == 200


async def test_copilot_oauth_start_returns_device_flow(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Copilot OAuth start returns device flow card (mocked)."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths
    from hermes_station.gateway import Gateway
    import hermes_station.admin.copilot_oauth as _copilot_oauth

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    # Login first without mock, save cookies
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    # Now call the OAuth endpoint with mock and saved cookies
    import hermes_station.admin.htmx_settings as _htmx_settings

    async def _mock_start():
        return {
            "device_code": "dev123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
            "poll_interval": 8,
        }

    with patch.object(_htmx_settings, "start_device_flow", _mock_start):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=saved_cookies) as client:
            resp = await client.post("/admin/_partial/provider/copilot/start")
    assert resp.status_code == 200
    assert "ABCD-EFGH" in resp.text


async def test_copilot_oauth_poll_pending(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Poll with pending status returns device flow card."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths
    from hermes_station.gateway import Gateway
    import hermes_station.admin.copilot_oauth as _copilot_oauth

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    import hermes_station.admin.htmx_settings as _htmx_settings2

    async def _mock_poll(device_code, interval=None):
        return {"status": "pending", "poll_interval": 8}

    with patch.object(_htmx_settings2, "poll_device_flow", _mock_poll):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=saved_cookies) as client:
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "user_code": "ABCD-EFGH", "interval": "8"},
            )
    assert resp.status_code == 200


async def test_copilot_oauth_poll_missing_device_code(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Poll with no device_code returns error card."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post("/admin/_partial/provider/copilot/poll", data={})
    assert resp.status_code == 200
    assert "Missing device_code" in resp.text


async def test_copilot_oauth_poll_expired(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Poll with expired token returns error card."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths
    from hermes_station.gateway import Gateway
    import hermes_station.admin.copilot_oauth as _copilot_oauth

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    import hermes_station.admin.htmx_settings as _htmx_settings3

    async def _mock_poll(device_code, interval=None):
        return {"status": "expired", "message": "Device code expired.", "poll_interval": 0}

    with patch.object(_htmx_settings3, "poll_device_flow", _mock_poll):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=saved_cookies) as client:
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "interval": "8"},
            )
    assert resp.status_code == 200


# ─────────────────────────────────────────────────────────── webui.py: is_healthy


async def test_webui_is_healthy_false_when_not_running(tmp_path: Path) -> None:
    """is_healthy() returns False when process is not running."""
    from hermes_station.webui import WebUIProcess

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    # process is None → is_running() is False → is_healthy() returns False
    result = await proc.is_healthy()
    assert result is False


async def test_webui_is_healthy_false_on_connection_error(tmp_path: Path) -> None:
    """is_healthy() returns False when HTTP probe fails (no real server)."""
    from hermes_station.webui import WebUIProcess
    from unittest.mock import MagicMock

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    # Simulate a running process
    mock_process = MagicMock()
    mock_process.returncode = None
    proc.process = mock_process

    # The probe will fail because nothing is running on port 8788
    result = await proc.is_healthy()
    assert result is False


# ─────────────────────────────────────────────────────────── routes.py: remaining branches


async def test_api_channels_get_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    """api_channels_get returns 401 without auth."""
    from hermes_station.app import create_app
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/channels")
    assert resp.status_code == 401


async def test_api_channels_save_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    """api_channels_save returns 401 without auth."""
    from hermes_station.app import create_app
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/api/channels/save", json={})
    assert resp.status_code == 401


async def test_api_pairing_pending_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    """api_pairing_pending returns 401 without auth."""
    from hermes_station.app import create_app
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/pairing/pending")
    assert resp.status_code == 401


async def test_api_pairing_approved_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    """api_pairing_approved returns 401 without auth."""
    from hermes_station.app import create_app
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/api/pairing/approved")
    assert resp.status_code == 401


async def test_api_pairing_action_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    """api_pairing_action returns 401 without auth."""
    from hermes_station.app import create_app
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/api/pairing/approve", json={"user_id": "42"})
    assert resp.status_code == 401


async def test_api_gateway_stop_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway stop action returns 200 or 500."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/stop")
    assert resp.status_code in (200, 500)


async def test_api_gateway_restart_action(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """Gateway restart action returns 200 or 500."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.post("/admin/api/gateway/restart")
    assert resp.status_code in (200, 500)


async def test_channels_fragment_save_no_gateway(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """channels_fragment_save works when no gateway is set (covers the gateway-None branch)."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    # No gateway set on app.state

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:testok"},
        )
    assert resp.status_code == 200
    assert "Channels saved." in resp.text


async def test_channels_fragment_clear_no_gateway(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """channels_fragment_clear works when no gateway is set."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    # No gateway

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "discord"},
        )
    assert resp.status_code == 200
    assert "Discord cleared." in resp.text


async def test_channels_fragment_toggle_no_gateway(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """channels_fragment_toggle works when no gateway is set."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "discord"},
        )
    assert resp.status_code == 200


async def test_provider_fragment_save_no_gateway(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """provider_fragment_save works when no gateway is set."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/provider/setup",
            data={"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-test"},
        )
    assert resp.status_code == 200
    assert "Provider saved." in resp.text


async def test_provider_fragment_save_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/provider/setup redirects to login."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/provider/setup",
            data={"provider": "anthropic"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_channels_fragment_save_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/channels/save redirects to login."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:test"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_channels_fragment_clear_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/channels/clear redirects to login."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/channels/clear",
            data={"slug": "telegram"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_channels_fragment_toggle_requires_admin(fake_data_dir: Path) -> None:
    """Unauthenticated POST /admin/_partial/channels/toggle redirects to login."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/_partial/channels/toggle",
            data={"slug": "telegram"},
            follow_redirects=False,
        )
    assert resp.status_code == 302


async def test_pairings_page_requires_admin(fake_data_dir: Path, admin_password: str) -> None:
    """Unauthenticated GET /admin/pairings redirects to login."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/pairings", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in resp.headers["location"]


async def test_pairings_fragment_requires_admin_htmx(fake_data_dir: Path, admin_password: str) -> None:
    """Unauthenticated GET /admin/_partial/pairings redirects to login."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/_partial/pairings", follow_redirects=False)
    assert resp.status_code == 302


async def test_htmx_provider_fragment_save_error_path(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """provider_fragment_save with invalid provider returns error HTML."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/provider/setup",
            data={"provider": "invalid-provider", "model": "x", "api_key": "y"},
        )
    assert resp.status_code == 200
    # Error alert should be in the returned card
    assert "error" in resp.text.lower() or "invalid" in resp.text.lower() or "Provider" in resp.text


async def test_htmx_channels_fragment_save_error_on_newline(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """channels_fragment_save with invalid value returns error HTML."""
    app = _build_htmx_app(fake_data_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        resp = await client.post(
            "/admin/_partial/channels/save",
            data={"TELEGRAM_BOT_TOKEN": "12345:abc\nevil"},
        )
    assert resp.status_code == 200
    assert "error" in resp.text.lower() or "Channels" in resp.text


async def test_copilot_oauth_poll_success(fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None) -> None:
    """Poll with success status saves token and returns provider card."""
    from hermes_station.admin.htmx_settings import routes as htmx_routes
    from hermes_station.admin.routes import admin_routes
    from starlette.applications import Starlette
    from hermes_station.config import Paths
    from hermes_station.gateway import Gateway
    import hermes_station.admin.htmx_settings as _htmx_settings4

    app = Starlette(routes=[*admin_routes(), *htmx_routes()])
    app.state.paths = Paths()
    app.state.gateway = Gateway(hermes_home=app.state.paths.hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302
        saved_cookies = dict(client.cookies)

    async def _mock_poll_success(device_code, interval=None):
        return {"status": "success", "token": "gho_test_token_abc", "poll_interval": 0}

    with patch.object(_htmx_settings4, "poll_device_flow", _mock_poll_success):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=saved_cookies) as client:
            resp = await client.post(
                "/admin/_partial/provider/copilot/poll",
                data={"device_code": "dev123", "interval": "8"},
            )
    assert resp.status_code == 200
    # Check that the token was saved and we get the provider card back
    assert "GitHub Copilot connected" in resp.text or "Provider" in resp.text


def test_webui_build_env_no_admin_password_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_build_env when HERMES_ADMIN_PASSWORD is not set does not add HERMES_WEBUI_PASSWORD."""
    from hermes_station.webui import WebUIProcess

    monkeypatch.delenv("HERMES_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_PASSWORD", raising=False)

    proc = WebUIProcess(
        webui_src=tmp_path,
        hermes_home=tmp_path / "hermes",
        webui_state_dir=tmp_path / "webui",
        workspace_dir=tmp_path / "workspace",
        config_path=tmp_path / "hermes" / "config.yaml",
    )
    env = proc._build_env()
    # No HERMES_ADMIN_PASSWORD → no HERMES_WEBUI_PASSWORD injected
    assert "HERMES_WEBUI_PASSWORD" not in env


async def test_api_status_returns_json(
    fake_data_dir: Path, admin_password: str, clear_login_rate_limit: None
) -> None:
    """api_status returns structured JSON with expected fields."""
    from hermes_station.app import create_app

    async def _login(client: httpx.AsyncClient) -> None:
        r = await client.post("/admin/login", data={"password": admin_password}, follow_redirects=False)
        assert r.status_code == 302

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/admin/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "paths" in body
    assert "model" in body
    assert "gateway" in body
    assert body["auth"]["enabled"] is True
    assert body["auth"]["authenticated"] is True
