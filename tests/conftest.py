"""Shared pytest fixtures.

Most tests run against a temporary /data tree (no container required).
Container-based integration tests are gated on `HERMES_STATION_CONTAINER_TESTS=1`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def fake_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway /data tree, populated to match the post-first-boot contract."""
    data = tmp_path / "data"
    hermes_home = data / ".hermes"
    (hermes_home / "pairing").mkdir(parents=True)
    (hermes_home / "sessions").mkdir()
    (hermes_home / "skills").mkdir()
    (hermes_home / "optional-skills").mkdir()
    (data / "webui").mkdir()
    (data / "workspace").mkdir()

    for name in ("telegram-approved.json", "telegram-pending.json", "_rate_limits.json"):
        (hermes_home / "pairing" / name).write_text("{}")

    monkeypatch.setenv("HOME", str(data))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(hermes_home / "config.yaml"))
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(data / "webui"))
    monkeypatch.setenv("HERMES_WORKSPACE_DIR", str(data / "workspace"))

    yield data


@pytest.fixture
def admin_password(monkeypatch: pytest.MonkeyPatch) -> str:
    password = "test-admin-pw"
    monkeypatch.setenv("HERMES_ADMIN_PASSWORD", password)
    return password


@pytest.fixture(autouse=True)
def _clear_login_rate_limit() -> Iterator[None]:
    """Reset the in-process login rate-limit counter before every test.

    _login_attempts in admin/routes.py is module-level state. With random
    test ordering (pytest-randomly), wrong-password tests from different
    files accumulate against the same 'unknown' client IP and exhaust the
    10-per-60s limit before legitimate login tests run, causing 429s.
    """
    from hermes_station.admin import routes as _routes

    _routes._login_attempts.clear()
    yield
    _routes._login_attempts.clear()
