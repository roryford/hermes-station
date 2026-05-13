"""GitHub Copilot OAuth device code flow.

Client ID and flow match the Copilot CLI / opencode implementation so the
resulting gho_ token is accepted by the Copilot API.
"""

from __future__ import annotations

import os

import httpx

COPILOT_OAUTH_CLIENT_ID = os.getenv("COPILOT_OAUTH_CLIENT_ID", "Ov23li8tweQw6odWQebz")
_SCOPE = "read:user"
_POLL_SAFETY_MARGIN = 3  # seconds added to GitHub's requested interval

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "hermes-station/1.0",
}


async def start_device_flow() -> dict:
    """Request a device code from GitHub.

    Returns the raw GitHub response dict:
        {device_code, user_code, verification_uri, expires_in, interval}
    Raises ValueError on HTTP or parse error.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _DEVICE_CODE_URL,
            content=f"client_id={COPILOT_OAUTH_CLIENT_ID}&scope={_SCOPE}",
            headers=_HEADERS,
        )
    resp.raise_for_status()
    data = resp.json()
    if "device_code" not in data or "user_code" not in data:
        raise ValueError(f"Unexpected response from GitHub: {data}")
    data["poll_interval"] = int(data.get("interval", 5)) + _POLL_SAFETY_MARGIN
    return data


async def poll_device_flow(device_code: str, interval: int | None = None) -> dict:
    """Poll GitHub for an access token.

    Returns a dict with:
        status: "pending" | "slow_down" | "success" | "expired" | "denied" | "error"
        token:  access token (only when status == "success")
        poll_interval: seconds to wait before next poll
        message: human-readable description (on error/denied/expired)
    """
    payload = (
        f"client_id={COPILOT_OAUTH_CLIENT_ID}"
        f"&device_code={device_code}"
        f"&grant_type=urn:ietf:params:oauth:grant-type:device_code"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(_ACCESS_TOKEN_URL, content=payload, headers=_HEADERS)

    try:
        data = resp.json()
    except Exception:
        return {"status": "error", "message": f"HTTP {resp.status_code}", "poll_interval": 10}

    if data.get("access_token"):
        return {"status": "success", "token": data["access_token"], "poll_interval": 0}

    error = data.get("error", "")
    server_interval = int(data.get("interval", interval or 5))

    if error == "authorization_pending":
        return {"status": "pending", "poll_interval": server_interval + _POLL_SAFETY_MARGIN}
    if error == "slow_down":
        return {"status": "slow_down", "poll_interval": server_interval + _POLL_SAFETY_MARGIN}
    if error == "expired_token":
        return {"status": "expired", "message": "Device code expired — please try again.", "poll_interval": 0}
    if error == "access_denied":
        return {"status": "denied", "message": "Authorization was denied.", "poll_interval": 0}

    return {
        "status": "error",
        "message": data.get("error_description") or error or "Unknown error from GitHub.",
        "poll_interval": 0,
    }
