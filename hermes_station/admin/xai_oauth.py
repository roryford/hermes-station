"""xAI (SuperGrok) OAuth 2.0 + PKCE flow.

SuperGrok / X Premium+ uses a standard OAuth 2.0 authorization code flow with
PKCE (Proof Key for Code Exchange, S256 method). The user's browser is
redirected to auth.x.ai, authorizes there, then xAI attempts to redirect to
http://127.0.0.1:56121/callback — the localhost redirect URI registered for the
public Grok CLI client. Since no local listener is running, xAI shows the
authorization code on screen. The user copies it and pastes it into the admin UI,
where hermes exchanges it server-side using the stored code_verifier.

State, code_verifier, and code_challenge are kept in an in-memory dict keyed by
a random state token (secrets.token_urlsafe). Entries expire after 10 minutes.
A separate _latest_flow dict holds the most recent pending flow so the paste-code
endpoint can retrieve the verifier without needing the state token.

XAI_OAUTH_CLIENT_ID is resolved once in start_pkce_flow. The default is the
public desktop client ID used by the Grok CLI — not a secret.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

# Public desktop OAuth client ID for the Grok CLI flow — not a secret.
# Matches the value used by opencode-grok-auth and similar implementations.
# Override via XAI_OAUTH_CLIENT_ID env var if needed.
_DEFAULT_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"

# Redirect URI registered for the public Grok CLI client. The station is not
# listening on this port — when xAI tries to redirect here and fails, it
# displays the authorization code on screen for the user to copy and paste back.
_REDIRECT_URI = "http://127.0.0.1:56121/callback"

_AUTH_URL = "https://auth.x.ai/oauth2/authorize"
_TOKEN_URL = "https://auth.x.ai/oauth2/token"
_SCOPES = "openid profile email offline_access grok-cli:access api:access"
_STATE_TTL_SECONDS = 600  # 10 minutes

_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "hermes-station/1.0",
}

# Pending PKCE states: state_token -> {code_verifier, code_challenge, redirect_uri, client_id, expires_at}
_pending_states: dict[str, dict[str, str | float]] = {}

# Most recent pending flow — used by the paste-code exchange path.
_latest_flow: dict[str, str | float] = {}


def _purge_expired_states() -> None:
    now = time.monotonic()
    expired = [k for k, v in _pending_states.items() if float(v["expires_at"]) < now]
    for k in expired:
        del _pending_states[k]


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    raw = secrets.token_bytes(32)
    code_verifier = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_authorize_url(
    state: str, code_challenge: str, nonce: str, redirect_uri: str, client_id: str
) -> str:
    """Build the xAI authorization URL."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
        "plan": "generic",
        "referrer": "hermes-agent",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def start_pkce_flow() -> tuple[str, str]:
    """Generate PKCE pair and state, register in pending dicts.

    Returns (state, authorize_url). Uses the fixed localhost redirect URI
    registered for the public Grok CLI client. Purges expired entries on each call.
    """
    global _latest_flow
    _purge_expired_states()
    client_id = os.environ.get("XAI_OAUTH_CLIENT_ID", "") or _DEFAULT_CLIENT_ID
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_hex(24)
    code_verifier, code_challenge = generate_pkce_pair()
    authorize_url = build_authorize_url(state, code_challenge, nonce, _REDIRECT_URI, client_id)
    expires_at = time.monotonic() + _STATE_TTL_SECONDS
    entry: dict[str, str | float] = {
        "code_verifier": code_verifier,
        "code_challenge": code_challenge,
        "redirect_uri": _REDIRECT_URI,
        "client_id": client_id,
        "expires_at": expires_at,
    }
    _pending_states[state] = entry
    _latest_flow = dict(entry)
    return state, authorize_url


def consume_state(state: str) -> dict[str, str]:
    """Pop and return the pending state entry, or raise ValueError if missing/expired."""
    entry = _pending_states.pop(state, None)
    if entry is None:
        raise ValueError("Invalid or expired OAuth state. Please start the flow again.")
    if time.monotonic() > float(entry["expires_at"]):
        raise ValueError("OAuth state expired. Please start the flow again.")
    return {k: str(v) for k, v in entry.items() if k != "expires_at"}


def pop_latest_flow() -> dict[str, str]:
    """Return and clear the latest pending PKCE flow for paste-code exchange.

    Raises ValueError if no active flow or if it has expired.
    """
    global _latest_flow
    if not _latest_flow:
        raise ValueError("No active xAI OAuth flow. Please click 'Connect with xAI' first.")
    if time.monotonic() > float(_latest_flow["expires_at"]):
        _latest_flow = {}
        raise ValueError("xAI OAuth session expired (10 min limit). Please start again.")
    flow = {k: str(v) for k, v in _latest_flow.items() if k != "expires_at"}
    _latest_flow = {}
    return flow


async def exchange_code(
    code: str,
    code_verifier: str,
    code_challenge: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """Exchange authorization code for tokens.

    Returns the raw token response dict: {access_token, token_type, expires_in, ...}
    Raises ValueError on HTTP or parse error.
    """
    payload = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
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


def write_xai_auth_json(hermes_home: Path, token_data: dict) -> None:
    """Persist xAI OAuth tokens to auth.json under the 'xai-oauth' provider key.

    Merges into any existing auth.json so other provider entries are preserved.
    File is written 0600.
    """
    auth_path = hermes_home / "auth.json"
    try:
        existing: dict = json.loads(auth_path.read_text()) if auth_path.exists() else {}
    except Exception:
        existing = {}

    expires_in = int(token_data.get("expires_in") or 3600)
    existing["xai-oauth"] = {
        "accessToken": token_data["access_token"],
        "refreshToken": token_data.get("refresh_token", ""),
        "expiresAt": int(time.time()) + expires_in,
        "idToken": token_data.get("id_token", ""),
        "tokenType": token_data.get("token_type", "Bearer"),
    }
    auth_path.write_text(json.dumps(existing, indent=2))
    auth_path.chmod(0o600)
