"""Shared Jinja2Templates instance for the admin UI.

Centralised so the ``build_tag`` cache-bust global only has to be installed
once. Each admin module imports ``templates`` from here.
"""

from __future__ import annotations

import time
from pathlib import Path

from starlette.templating import Jinja2Templates

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

BUILD_TAG = str(int(time.time()))

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
templates.env.globals["build_tag"] = BUILD_TAG
