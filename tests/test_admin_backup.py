"""Tests for hermes_station.admin.backup."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
from starlette.applications import Starlette

from hermes_station.admin.backup import (
    _build_archive,
    _checkpoint_db,
    routes as backup_routes,
)
from hermes_station.admin.routes import admin_routes
from hermes_station.config import Paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(gateway: object = None, hermes_home: Path | None = None) -> Starlette:
    app = Starlette(routes=[*admin_routes(), *backup_routes()])
    paths = MagicMock(spec=Paths)
    paths.hermes_home = hermes_home or Path("/nonexistent/.hermes")
    app.state.paths = paths
    app.state.gateway = gateway
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    resp = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert resp.status_code == 302, resp.text


def _make_gateway() -> MagicMock:
    gw = MagicMock()
    gw.stop = AsyncMock()
    gw.start = AsyncMock()
    return gw


# ---------------------------------------------------------------------------
# Unit: _build_archive
# ---------------------------------------------------------------------------


def test_build_archive_includes_existing_files(tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("SECRET=abc")
    (hermes_home / "config.yaml").write_text("model: {}")

    raw = _build_archive(hermes_home)

    buf = io.BytesIO(raw)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
    assert ".env" in names
    assert "config.yaml" in names


def test_build_archive_skips_missing_files(tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    # Only write one file; others should be silently skipped.
    (hermes_home / "SOUL.md").write_text("Be helpful.")

    raw = _build_archive(hermes_home)

    buf = io.BytesIO(raw)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
    assert "SOUL.md" in names
    assert ".env" not in names


def test_build_archive_is_valid_gzip(tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("X=1")

    raw = _build_archive(hermes_home)

    # First bytes should be gzip magic.
    assert raw[:2] == b"\x1f\x8b"


# ---------------------------------------------------------------------------
# Unit: _checkpoint_db
# ---------------------------------------------------------------------------


def test_checkpoint_db_no_op_when_missing(tmp_path: Path) -> None:
    # Should not raise even if the file doesn't exist.
    _checkpoint_db(tmp_path / "state.db")


def test_checkpoint_db_creates_wal_checkpoint(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.commit()
    conn.close()

    # Should run without raising.
    _checkpoint_db(db_path)


# ---------------------------------------------------------------------------
# Integration: /admin/backup page (GET)
# ---------------------------------------------------------------------------


async def test_backup_page_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/backup", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"


async def test_backup_page_renders_after_login(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/backup")
    assert resp.status_code == 200
    assert "Backup" in resp.text
    assert "Download backup" in resp.text
    assert "Restore from backup" in resp.text


async def test_backup_page_shows_security_warning(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/backup")
    assert resp.status_code == 200
    assert "API keys" in resp.text or "secrets" in resp.text.lower()


# ---------------------------------------------------------------------------
# Integration: download endpoint
# ---------------------------------------------------------------------------


async def test_download_unauthenticated_returns_401(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/api/backup/download")
    assert resp.status_code == 401


async def test_download_returns_valid_tar_gz(fake_data_dir: Path, admin_password: str, tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("OPENAI_API_KEY=sk-test")
    (hermes_home / "config.yaml").write_text("model: {}")

    gw = _make_gateway()
    app = _build_app(gateway=gw, hermes_home=hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post("/admin/api/backup/download")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    assert "attachment" in resp.headers["content-disposition"]
    assert "hermes-station-backup-" in resp.headers["content-disposition"]

    # Verify it's actually a valid tar.gz.
    buf = io.BytesIO(resp.content)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
    assert ".env" in names
    assert "config.yaml" in names


async def test_download_calls_gateway_stop_and_start(fake_data_dir: Path, admin_password: str, tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    gw = _make_gateway()
    app = _build_app(gateway=gw, hermes_home=hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        await client.post("/admin/api/backup/download")

    gw.stop.assert_called_once()
    gw.start.assert_called_once()


# ---------------------------------------------------------------------------
# Integration: restore endpoint
# ---------------------------------------------------------------------------


def _make_tar_gz(files: dict[str, str]) -> bytes:
    """Build an in-memory tar.gz with the given {name: content} mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            encoded = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(encoded)
            tar.addfile(info, io.BytesIO(encoded))
    return buf.getvalue()


async def test_restore_unauthenticated_returns_401(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/api/backup/restore")
    assert resp.status_code == 401


async def test_restore_no_file_returns_400(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post("/admin/api/backup/restore")
    assert resp.status_code == 400
    assert "No file" in resp.text


async def test_restore_corrupt_archive_returns_400(fake_data_dir: Path, admin_password: str) -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", b"this is not gzip data", "application/gzip")},
        )
    assert resp.status_code == 400
    assert "Invalid archive" in resp.text


async def test_restore_path_traversal_absolute_rejected(fake_data_dir: Path, admin_password: str) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        content = b"evil"
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    archive = buf.getvalue()

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", archive, "application/gzip")},
        )
    assert resp.status_code == 400
    assert "unexpected entry" in resp.text


async def test_restore_path_traversal_dotdot_rejected(fake_data_dir: Path, admin_password: str) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        content = b"evil"
        info = tarfile.TarInfo(name="../../etc/passwd")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    archive = buf.getvalue()

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", archive, "application/gzip")},
        )
    assert resp.status_code == 400
    assert "unexpected entry" in resp.text


async def test_restore_non_allowlisted_file_rejected(fake_data_dir: Path, admin_password: str, tmp_path: Path) -> None:
    """A crafted archive with an unexpected (non-allowlisted) filename must be rejected."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    archive = _make_tar_gz({"evil.py": "print('pwned')"})

    app = _build_app(hermes_home=hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", archive, "application/gzip")},
        )

    assert resp.status_code == 400
    assert "unexpected entry" in resp.text
    # Nothing should have been written.
    assert not (hermes_home / "evil.py").exists()


async def test_restore_subdir_filename_rejected(fake_data_dir: Path, admin_password: str, tmp_path: Path) -> None:
    """Even a 'safe' subdirectory path is rejected — restore is strictly flat-allowlist."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    archive = _make_tar_gz({"subdir/config.yaml": "model: {}"})

    app = _build_app(hermes_home=hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", archive, "application/gzip")},
        )

    assert resp.status_code == 400
    assert "unexpected entry" in resp.text


async def test_restore_empty_archive_rejected(fake_data_dir: Path, admin_password: str, tmp_path: Path) -> None:
    """An archive with no restorable files is rejected (rather than silently no-op)."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    archive = _make_tar_gz({})

    app = _build_app(hermes_home=hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", archive, "application/gzip")},
        )

    assert resp.status_code == 400
    assert "no restorable files" in resp.text


async def test_restore_success(fake_data_dir: Path, admin_password: str, tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    archive = _make_tar_gz({".env": "RESTORED=yes", "config.yaml": "model: {}"})

    gw = _make_gateway()
    app = _build_app(gateway=gw, hermes_home=hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", archive, "application/gzip")},
        )

    assert resp.status_code == 200
    assert "Restore complete" in resp.text

    # Files should have been written.
    assert (hermes_home / ".env").read_text() == "RESTORED=yes"
    assert (hermes_home / "config.yaml").read_text() == "model: {}"


async def test_restore_calls_gateway_stop_and_start(fake_data_dir: Path, admin_password: str, tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    archive = _make_tar_gz({"config.yaml": "model: {}"})

    gw = _make_gateway()
    app = _build_app(gateway=gw, hermes_home=hermes_home)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        await client.post(
            "/admin/api/backup/restore",
            files={"backup_file": ("backup.tar.gz", archive, "application/gzip")},
        )

    gw.stop.assert_called_once()
    gw.start.assert_called_once()
