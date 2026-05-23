"""xAI (SuperGrok) OAuth 2.0 + PKCE flow.

SuperGrok / X Premium+ uses a standard OAuth 2.0 authorization code flow with
PKCE (Proof Key for Code Exchange, S256 method). The user's browser is
redirected to auth.x.ai, authorizes there, and xAI redirects back to the
station's callback URL. The station exchanges the code server-side.

State and code_verifier are kept in an in-memory dict keyed by a random state
token (secrets.token_urlsafe). Entries expire after 10 minutes. This is fine
for a single-admin-user setup where the station runs in one process.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

# Public OAuth client ID for SuperGrok.
# Override via XAI_OAUTH_CLIENT_ID env var if your xAI app uses a different ID.
# xAI's public SuperGrok OAuth client ID is not yet publicly documented with a
# default value, so this MUST be configured via the environment variable.
XAI_OAUTH_CLIENT_ID = os.getenv("XAI_OAUTH_CLIENT_ID", "")

_AUTH_URL = "https://auth.x.ai/oauth2/authorize"
_TOKEN_URL = "https://auth.x.ai/oauth2/token"
_SCOPES = "openid offline_access"
_STATE_TTL_SECONDS = 600  # 10 minutes

_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "hermes-station/1.0",
}

# Pending PKCE states: state_token -> {code_verifier, redirect_uri, expires_at}
_pending_states: dict[str, dict[str, str | float]] = {}


def _purge_expired_states() -> None:
    """Remove state entries older than _STATE_TTL_SECONDS."""
    now = time.monotonic()
    expired = [k for k, v in _pending_states.items() if float(v["expires_at"]) < now]
    for k in expired:
        del _pending_states[k]


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method.

    code_verifier: 32 random URL-safe bytes, base64url-encoded (no padding).
    code_challenge: SHA-256 of the verifier, base64url-encoded (no padding).
    """
    raw = secrets.token_bytes(32)
    code_verifier = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_authorize_url(state: str, code_challenge: str, redirect_uri: str) -> str:
    """Build the xAI authorization URL.

    Raises ValueError if XAI_OAUTH_CLIENT_ID is not configured.
    """
    client_id = XAI_OAUTH_CLIENT_ID
    if not client_id:
        raise ValueError(
            "XAI_OAUTH_CLIENT_ID is not set. "
            "Set this environment variable to your xAI OAuth application's client ID."
        )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def start_pkce_flow(redirect_uri: str) -> tuple[str, str]:
    """Generate PKCE pair and state, register in pending dict.

    Returns (state, authorize_url). Raises ValueError if client_id not set.
    Purges expired entries on each call.
    """
    _purge_expired_states()
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()
    _pending_states[state] = {
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "expires_at": time.monotonic() + _STATE_TTL_SECONDS,
    }
    authorize_url = build_authorize_url(state, code_challenge, redirect_uri)
    return state, authorize_url


def consume_state(state: str) -> dict[str, str]:
    """Pop and return the pending state entry, or raise ValueError if missing/expired.

    Validates TTL on retrieval even if purge hasn't run yet.
    """
    entry = _pending_states.pop(state, None)
    if entry is None:
        raise ValueError("Invalid or expired OAuth state. Please start the flow again.")
    if time.monotonic() > float(entry["expires_at"]):
        raise ValueError("OAuth state expired. Please start the flow again.")
    return {k: str(v) for k, v in entry.items() if k != "expires_at"}


async def exchange_code(code: str, code_verifier: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens.

    Returns the raw token response dict:
        {access_token, token_type, expires_in, refresh_token, ...}
    Raises ValueError on HTTP or parse error.
    """
    client_id = XAI_OAUTH_CLIENT_ID
    if not client_id:
        raise ValueError("XAI_OAUTH_CLIENT_ID is not set.")
    payload = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(_TOKEN_URL, content=payload, headers=_HEADERS)
    try:
        data = resp.json()
    except Exception:
        raise ValueError(f"xAI token endpoint returned non-JSON (HTTP {resp.status_code})")
    if not resp.is_success:
        err = data.get("error_description") or data.get("error") or f"HTTP {resp.status_code}"
        raise ValueError(f"xAI token exchange failed: {err}")
    if "access_token" not in data:
        raise ValueError(f"xAI token response missing access_token: {data}")
    return data
