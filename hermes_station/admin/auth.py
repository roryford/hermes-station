"""Admin password auth with signed session cookies.

Single-user product: one shared admin password, no user accounts. Session is a
signed cookie (itsdangerous) carrying an expiry timestamp. No server-side
session store needed.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from hermes_station.config import AdminSettings

COOKIE_NAME = "hermes_station_admin"


@dataclass(frozen=True)
class AuthState:
    enabled: bool
    authenticated: bool


def _signer() -> TimestampSigner:
    from hermes_station.config import Paths, load_or_create_signing_key
    try:
        paths = Paths()
        key = load_or_create_signing_key(paths)
        return TimestampSigner(key, salt=b"hermes-station-admin")
    except Exception:
        # Fallback for test environments where /data doesn't exist.
        settings = AdminSettings()
        secret = settings.effective_admin_password or "hermes-station-unconfigured"
        return TimestampSigner(secret.encode("utf-8"), salt="hermes-station-admin")


def admin_auth_enabled() -> bool:
    return bool(AdminSettings().effective_admin_password)


def verify_password(submitted: str) -> bool:
    expected = AdminSettings().effective_admin_password
    if not expected:
        return False
    return hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))


def issue_session_cookie(response: Response, request: Request) -> None:
    settings = AdminSettings()
    signed = _signer().sign(str(int(time.time()))).decode("utf-8")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).lower()
    secure = proto == "https"
    response.set_cookie(
        COOKIE_NAME,
        signed,
        max_age=settings.admin_session_ttl,
        httponly=True,
        samesite="strict",
        secure=secure,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def is_authenticated(request: Request) -> bool:
    if not admin_auth_enabled():
        return False
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return False
    try:
        _signer().unsign(cookie, max_age=AdminSettings().admin_session_ttl)
    except (BadSignature, SignatureExpired):
        return False
    return True


def auth_state(request: Request) -> AuthState:
    return AuthState(enabled=admin_auth_enabled(), authenticated=is_authenticated(request))


def require_admin(request: Request) -> Response | None:
    """Return an early-exit response if the request lacks valid admin auth."""
    if is_authenticated(request):
        return None
    if request.url.path.startswith("/admin/api/"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return RedirectResponse(url="/admin/login", status_code=302)
