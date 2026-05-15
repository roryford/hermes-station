"""Tests for hermes_station.admin.copilot_oauth — device flow (mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from hermes_station.admin.copilot_oauth import (
    poll_device_flow,
    start_device_flow,
)


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
    mock_client = _make_mock_client(
        {
            "device_code": "dev123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }
    )
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
    mock_client = _make_mock_client(
        {"error": "some_weird_error", "error_description": "Something went wrong"}
    )
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
