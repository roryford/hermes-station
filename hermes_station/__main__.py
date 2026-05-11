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
    )


if __name__ == "__main__":
    main()
