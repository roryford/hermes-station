"""Entry point: `python -m hermes_station` boots the ASGI server."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "hermes_station.app:app",
        host=os.getenv("CONTROL_PLANE_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8787")),
        log_config=None,
        access_log=True,
        # Trust X-Forwarded-* from any peer. The station only ever runs behind
        # a managed edge (Railway / Cloudflare); without this, request.url.scheme
        # stays "http" on HTTPS deployments, which propagates through the proxy
        # as X-Forwarded-Proto: http and tells hermes-webui to skip the Secure
        # flag on its session cookie.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
