"""Entry point: `python -m hermes_station` boots the ASGI server."""

from __future__ import annotations

import argparse
import os

import uvicorn

from hermes_station import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hermes-station",
        description="hermes-station control plane. All config via environment variables.",
        epilog="See https://github.com/roryford/hermes-station for the full env-var reference.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args()

    # TRUSTED_PROXY_IPS should be set to the actual proxy IP(s) for Railway/Cloudflare
    # deployments. Defaults to loopback-only to prevent header spoofing from arbitrary clients.
    trusted_ips = os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1")
    uvicorn.run(
        "hermes_station.app:app",
        host=os.getenv("CONTROL_PLANE_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8787")),
        log_config=None,
        access_log=True,
        proxy_headers=True,
        forwarded_allow_ips=trusted_ips,
    )


if __name__ == "__main__":
    main()
