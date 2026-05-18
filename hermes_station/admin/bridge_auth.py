"""Webui session bridge auth.

Verifies a hermes-webui ``hermes_session`` cookie by making an internal
loopback call to ``http://127.0.0.1:8788/api/auth/status``.

Endpoint choice rationale
-------------------------
``/api/auth/status`` returns BOTH ``auth_enabled`` and ``logged_in`` fields.
The ``logged_in`` boolean distinguishes "auth is configured but not signed in"
from "auth is configured and the cookie is a valid session". We use it and
key off ``logged_in == True``. This is preferable to probing other endpoints
because:

  - it's an explicitly auth-introspection endpoint, semantically intended for this
  - it works whether or not auth is enabled (degrades gracefully)
  - it does not perform any side effects or expensive work

Contract dependency
-------------------
This module depends on hermes-webui's ``/api/auth/status`` returning JSON of
shape ``{auth_enabled: bool, logged_in: bool}``. That contract is tracked in
``docs/CONTRACT.md`` §10 (webui internal-contract dependency).

Pooled HTTP client
------------------
A pooled ``httpx.AsyncClient`` is stored on ``app.state.bridge_http_client``
by the app lifespan (mirroring ``hermes_station/proxy.py``'s pooled client).
The client is configured with ``base_url`` empty — we use the absolute
``_WEBUI_AUTH_STATUS_URL`` constant so the bridge is self-contained and the
client can be reused by other webui-loopback callers in the future.

Logging policy
--------------
Successful verifications are silent. Failure paths (timeout, non-2xx,
malformed JSON, missing ``logged_in`` field) log at INFO. Raw cookie values
are NEVER logged — only the cookie *names* present in the request header
are surfaced for debugging (values are replaced with ``***``).
"""

from __future__ import annotations

import logging

import httpx
from starlette.requests import Request

logger = logging.getLogger(__name__)

_WEBUI_AUTH_STATUS_URL = "http://127.0.0.1:8788/api/auth/status"
_WEBUI_COOKIE_NAME = "hermes_session"
_TIMEOUT_SECONDS = 2.0


# ─────────────────────────────────────────────────────── failure counters
#
# Process-local counters surfaced via ``/health`` so operators can spot
# bridge degradation without grepping logs. Intentionally a plain dict (no
# Prometheus dep) — the /health endpoint is the only consumer.
#
# Keys are intentionally exhaustive and stable: any new failure path must
# pick one of these buckets or add a new key here AND in the /health shape.

_FAILURE_BUCKETS = ("timeout", "http_4xx", "http_5xx", "malformed_json", "missing_field")

_bridge_failures_total: dict[str, int] = {bucket: 0 for bucket in _FAILURE_BUCKETS}


def _record_failure(bucket: str) -> None:
    """Increment a failure counter. Unknown buckets are ignored (defensive)."""
    if bucket in _bridge_failures_total:
        _bridge_failures_total[bucket] += 1


def get_bridge_failures_total() -> dict[str, int]:
    """Return a snapshot copy of the per-type bridge failure counters."""
    return dict(_bridge_failures_total)


def reset_bridge_failures_total() -> None:
    """Reset counters to zero. Test-only helper."""
    for bucket in _FAILURE_BUCKETS:
        _bridge_failures_total[bucket] = 0


def _redacted_cookie_names(cookie_header: str) -> str:
    """Return a redacted list of cookie names from a Cookie header.

    Never returns values — only names, each annotated with ``=***``. Safe to
    include in log messages. Mirrors the cookie-name set pattern used in
    ``hermes_station/proxy.py`` (``_OUR_COOKIES``) but redacts values.
    """
    if not cookie_header:
        return ""
    names: list[str] = []
    for raw in cookie_header.split(";"):
        name = raw.split("=", 1)[0].strip()
        if name:
            names.append(f"{name}=***")
    return ", ".join(names)


async def verify_webui_session(request: Request) -> bool:
    """Return True iff the request carries a valid webui ``hermes_session`` cookie.

    Forwards the incoming ``Cookie`` header verbatim to the loopback
    ``/api/auth/status`` endpoint and returns True only when the JSON response
    has ``logged_in: true``.

    Short-circuits BEFORE making any HTTP call when the Cookie header is empty
    or doesn't contain ``hermes_session=``. This is both a perf win (no
    loopback for unauthenticated probes) and a safety guard.

    Any error path — timeout, non-2xx, malformed JSON, missing field, missing
    pooled client on app state — returns False. This function never raises.
    """
    cookie_header = request.headers.get("cookie", "")
    if not cookie_header or _WEBUI_COOKIE_NAME + "=" not in cookie_header:
        return False

    client: httpx.AsyncClient | None = getattr(request.app.state, "bridge_http_client", None)
    if client is None:
        logger.info(
            "bridge_auth: no pooled bridge_http_client on app.state; cookies=%s",
            _redacted_cookie_names(cookie_header),
        )
        return False

    try:
        resp = await client.get(
            _WEBUI_AUTH_STATUS_URL,
            headers={"Cookie": cookie_header},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        # Bucket as "timeout" for the dedicated timeout exception; everything
        # else httpx surfaces (connect error, read error, etc.) is folded
        # into http_5xx since from the caller's perspective the upstream is
        # effectively unavailable. Timeouts are the most common signal.
        if isinstance(exc, httpx.TimeoutException):
            _record_failure("timeout")
        else:
            _record_failure("http_5xx")
        logger.info(
            "bridge_auth: webui loopback failed: %s; cookies=%s",
            exc,
            _redacted_cookie_names(cookie_header),
        )
        return False

    if resp.status_code != 200:
        if 400 <= resp.status_code < 500:
            _record_failure("http_4xx")
        elif 500 <= resp.status_code < 600:
            _record_failure("http_5xx")
        logger.info(
            "bridge_auth: webui returned %d; cookies=%s",
            resp.status_code,
            _redacted_cookie_names(cookie_header),
        )
        return False

    try:
        data = resp.json()
    except ValueError as exc:
        _record_failure("malformed_json")
        logger.info(
            "bridge_auth: malformed JSON from webui: %s; cookies=%s",
            exc,
            _redacted_cookie_names(cookie_header),
        )
        return False

    if not isinstance(data, dict) or "logged_in" not in data:
        _record_failure("missing_field")
        logger.info(
            "bridge_auth: webui response missing logged_in field; cookies=%s",
            _redacted_cookie_names(cookie_header),
        )
        return False

    return bool(data.get("logged_in"))
