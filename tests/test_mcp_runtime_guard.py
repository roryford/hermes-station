"""Unit tests for the MCP runtime safety guard (issue #95).

Covers:
- Known unsafe launchers (npx, uvx, pipx) always produce a warning.
- Commands resolving under non-writable system paths produce no warning.
- Commands resolving under writable paths produce a warning.
- ``HERMES_STATION_STRICT_MCP_LAUNCHERS=1`` causes ``is_error=True`` and
  ``ready=False`` on the readiness row.
- Only *enabled* MCP servers are checked.
- URL-based (remote) servers are not checked.
- The warning rows surface in ``/admin/api/pilot/status`` JSON as ``mcp_servers``.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from hermes_station.readiness import (
    MCPServerWarning,
    check_mcp_runtime_safety,
    validate_readiness,
)


# ---------------------------------------------------------------------------
# check_mcp_runtime_safety — unit tests (no readiness plumbing)
# ---------------------------------------------------------------------------


def test_npx_launcher_always_warns() -> None:
    config = {
        "mcp_servers": {
            "my-server": {
                "command": "npx",
                "args": ["-y", "@some/mcp-server@latest"],
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.name == "my-server"
    assert w.command == "npx"
    assert "npx" in w.reason
    assert "writable cache" in w.reason
    assert w.is_error is False


def test_uvx_launcher_always_warns() -> None:
    config = {
        "mcp_servers": {
            "fetch": {
                "command": "uvx",
                "args": ["--from", "mcp-server-fetch==1.0.0", "mcp-server-fetch"],
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert len(warnings) == 1
    assert warnings[0].command == "uvx"


def test_pipx_launcher_always_warns() -> None:
    config = {
        "mcp_servers": {
            "tool": {
                "command": "pipx",
                "args": ["run", "some-mcp-tool"],
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert len(warnings) == 1
    assert warnings[0].command == "pipx"


def test_disabled_server_not_checked() -> None:
    """Disabled servers must be silently skipped."""
    config = {
        "mcp_servers": {
            "my-server": {
                "command": "npx",
                "args": [],
                "enabled": False,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert warnings == []


def test_url_based_server_not_checked() -> None:
    """Remote HTTP/SSE servers have no local command — skip them."""
    config = {
        "mcp_servers": {
            "playwright-mcp": {
                "url": "http://playwright.railway.internal:8931/mcp",
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert warnings == []


def test_no_mcp_servers_in_config() -> None:
    warnings = check_mcp_runtime_safety({})
    assert warnings == []


def test_mcp_servers_not_a_dict() -> None:
    warnings = check_mcp_runtime_safety({"mcp_servers": "broken"})
    assert warnings == []


def test_command_not_on_path_produces_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the command can't be resolved at all, we can't check the path — skip."""
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    config = {
        "mcp_servers": {
            "unknown-tool": {
                "command": "some-nonexistent-binary-xyz",
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert warnings == []


def test_command_resolves_to_nonwritable_system_path_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A binary installed in a root-owned, non-writable directory should pass."""
    # Create a fake binary under a directory the test user cannot write to.
    # We mock os.access to return False for all write checks.
    fake_bin = tmp_path / "fake-mcp-server"
    fake_bin.touch()

    monkeypatch.setattr("shutil.which", lambda _cmd: str(fake_bin))
    monkeypatch.setattr("os.access", lambda _path, _mode: False)

    config = {
        "mcp_servers": {
            "safe-server": {
                "command": "fake-mcp-server",
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert warnings == []


def test_command_resolves_to_writable_path_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A binary under a writable directory should trigger a warning."""
    fake_bin = tmp_path / "writable-mcp-server"
    fake_bin.touch()

    monkeypatch.setattr("shutil.which", lambda _cmd: str(fake_bin))
    # Simulate: the parent directory is writable.
    original_access = os.access

    def _mock_access(path: str, mode: int) -> bool:
        if mode == os.W_OK:
            return True
        return original_access(path, mode)  # type: ignore[arg-type]

    monkeypatch.setattr("os.access", _mock_access)

    config = {
        "mcp_servers": {
            "risky-server": {
                "command": "writable-mcp-server",
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.name == "risky-server"
    assert "writable" in w.reason
    assert w.is_error is False


def test_strict_mode_sets_is_error_true() -> None:
    config = {
        "mcp_servers": {
            "my-server": {
                "command": "npx",
                "args": [],
                "enabled": True,
            }
        }
    }
    warnings = check_mcp_runtime_safety(config, strict=True)
    assert len(warnings) == 1
    assert warnings[0].is_error is True


def test_as_dict_shape() -> None:
    w = MCPServerWarning(
        name="my-server",
        command="npx",
        reason="some warning",
        is_error=False,
    )
    d = w.as_dict()
    assert d["name"] == "my-server"
    assert d["command"] == "npx"
    assert d["reason"] == "some warning"
    assert d["is_error"] is False


# ---------------------------------------------------------------------------
# validate_readiness integration — mcp: rows
# ---------------------------------------------------------------------------


class _FakePaths:
    def __init__(self, hermes_home: Path) -> None:
        self.hermes_home = hermes_home


def test_validate_readiness_npx_server_adds_warning_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HERMES_STATION_STRICT_MCP_LAUNCHERS", raising=False)
    config = {
        "mcp_servers": {
            "my-server": {
                "command": "npx",
                "args": ["-y", "@some/pkg@latest"],
                "enabled": True,
            }
        }
    }
    rd = validate_readiness(_FakePaths(tmp_path), config, {})
    assert "mcp:my-server" in rd.readiness
    row = rd.readiness["mcp:my-server"]
    assert row.intended is True
    assert row.ready is True  # warning only, not error (no strict flag)
    assert "npx" in row.reason


def test_validate_readiness_safe_command_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command on a non-writable system path should not produce any mcp: row."""
    monkeypatch.delenv("HERMES_STATION_STRICT_MCP_LAUNCHERS", raising=False)
    # Mock shutil.which and os.access to simulate a safe, system-owned binary.
    monkeypatch.setattr("hermes_station.readiness.shutil.which", lambda _cmd: "/usr/bin/mcp-server-safe")
    monkeypatch.setattr("hermes_station.readiness.os.access", lambda _path, _mode: False)

    config = {
        "mcp_servers": {
            "safe-server": {
                "command": "mcp-server-safe",
                "enabled": True,
            }
        }
    }
    rd = validate_readiness(_FakePaths(tmp_path), config, {})
    mcp_keys = [k for k in rd.readiness if k.startswith("mcp:")]
    assert mcp_keys == []


def test_validate_readiness_strict_mode_ready_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_STRICT_MCP_LAUNCHERS", "1")
    config = {
        "mcp_servers": {
            "my-server": {
                "command": "npx",
                "args": [],
                "enabled": True,
            }
        }
    }
    rd = validate_readiness(_FakePaths(tmp_path), config, {})
    assert "mcp:my-server" in rd.readiness
    row = rd.readiness["mcp:my-server"]
    assert row.intended is True
    assert row.ready is False  # error state in strict mode
    assert rd.any_intended_not_ready() is True


def test_validate_readiness_strict_mode_flag_false_still_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without STRICT flag, npx server produces a warning row with ready=True."""
    monkeypatch.delenv("HERMES_STATION_STRICT_MCP_LAUNCHERS", raising=False)
    config = {
        "mcp_servers": {
            "my-server": {
                "command": "npx",
                "args": [],
                "enabled": True,
            }
        }
    }
    rd = validate_readiness(_FakePaths(tmp_path), config, {})
    row = rd.readiness.get("mcp:my-server")
    assert row is not None
    assert row.ready is True
    assert rd.any_intended_not_ready() is False


# ---------------------------------------------------------------------------
# /admin/api/pilot/status — mcp_servers field
# ---------------------------------------------------------------------------


async def _login(client: httpx.AsyncClient, password: str) -> None:
    resp = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert resp.status_code == 302


async def test_pilot_status_includes_mcp_servers_field(
    fake_data_dir: Path, admin_password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    monkeypatch.delenv("HERMES_STATION_STRICT_MCP_LAUNCHERS", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/api/pilot/status")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "mcp_servers" in data
    assert isinstance(data["mcp_servers"], list)


async def test_pilot_status_mcp_servers_has_warning_for_npx(
    fake_data_dir: Path, admin_password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When readiness cache has an mcp: warning row, the status payload
    includes it in mcp_servers with a reason string."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.admin.routes import admin_routes
    from hermes_station.readiness import CapabilityRow, Readiness
    from starlette.applications import Starlette

    app = Starlette(routes=admin_routes())
    app.state.readiness = Readiness(
        readiness={
            "mcp:my-npx-server": CapabilityRow(
                intended=True,
                ready=True,
                reason="MCP server 'my-npx-server' uses launcher 'npx' which executes code staged into a writable cache at runtime",
            )
        }
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/api/pilot/status")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    mcp = data.get("mcp_servers", [])
    assert isinstance(mcp, list)
    names = [entry["name"] for entry in mcp]
    assert "my-npx-server" in names
    entry = next(e for e in mcp if e["name"] == "my-npx-server")
    assert entry["reason"] is not None
    assert "npx" in entry["reason"]


async def test_pilot_status_strict_mode_mcp_server_is_error(
    fake_data_dir: Path, admin_password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With STRICT flag (is_error=True, ready=False), the mcp_servers entry reflects error state."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.admin.routes import admin_routes
    from hermes_station.readiness import CapabilityRow, Readiness
    from starlette.applications import Starlette

    # Simulate strict mode: ready=False (error, not just warning).
    app = Starlette(routes=admin_routes())
    app.state.readiness = Readiness(
        readiness={
            "mcp:risky": CapabilityRow(
                intended=True,
                ready=False,
                reason="MCP server 'risky' uses launcher 'npx' which executes code staged into a writable cache",
            )
        }
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/api/pilot/status")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    mcp = data.get("mcp_servers", [])
    entry = next((e for e in mcp if e["name"] == "risky"), None)
    assert entry is not None
    assert entry["is_error"] is True
    assert entry["ready"] is False
