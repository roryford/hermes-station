"""Tests for backup download and restore endpoints."""

from __future__ import annotations

import io
import sqlite3
import tarfile
from pathlib import Path

import httpx


# ── helpers ──────────────────────────────────────────────────────────────────


async def _login(client: httpx.AsyncClient, password: str) -> None:
    r = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert r.status_code == 302, r.text


class _FakeGateway:
    """Records stop/start calls. Optionally raises."""

    def __init__(
        self, *, stop_raises: Exception | None = None, start_raises: Exception | None = None
    ) -> None:
        self.stops = 0
        self.starts = 0
        self.stop_raises = stop_raises
        self.start_raises = start_raises

    async def stop(self) -> None:
        self.stops += 1
        if self.stop_raises is not None:
            raise self.stop_raises

    async def start(self) -> None:
        self.starts += 1
        if self.start_raises is not None:
            raise self.start_raises


def _seed_files(hermes_home: Path) -> None:
    (hermes_home / "config.yaml").write_text("provider: openrouter\n")
    db = hermes_home / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    (hermes_home / "memories").mkdir(exist_ok=True)
    (hermes_home / "memories" / "mem1.json").write_text("{}\n")
    (hermes_home / "pairing").mkdir(exist_ok=True)
    (hermes_home / "pairing" / "telegram-approved.json").write_text("[]\n")


def _build_valid_archive(files: dict[str, bytes]) -> bytes:
    """Build an in-memory tar.gz with the given filename→bytes mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


# ── download: unauthenticated ─────────────────────────────────────────────────


async def test_backup_download_401_when_not_authenticated(fake_data_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/api/pilot/backup/download")

    assert r.status_code == 401


# ── download: flag off ────────────────────────────────────────────────────────


async def test_backup_download_404_when_flag_off(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post("/admin/api/pilot/backup/download")

    assert r.status_code == 404


# ── download: success ─────────────────────────────────────────────────────────


async def test_backup_download_returns_valid_targz(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    hermes_home = fake_data_dir / ".hermes"
    _seed_files(hermes_home)

    fake_gw = _FakeGateway()

    from hermes_station.app import create_app

    app = create_app()
    app.state.gateway = fake_gw
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post("/admin/api/pilot/backup/download")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/gzip"
    cd = r.headers.get("content-disposition", "")
    assert "hermes-station-backup-" in cd
    assert cd.endswith('.tar.gz"')

    # Archive must be valid and contain expected files.
    buf = io.BytesIO(r.content)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        names = tf.getnames()
    assert "config.yaml" in names
    assert "state.db" in names
    assert any(n.startswith("memories") for n in names)
    assert any(n.startswith("pairing") for n in names)
    assert ".env" not in names


async def test_backup_download_calls_gateway_stop_and_start(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    hermes_home = fake_data_dir / ".hermes"
    _seed_files(hermes_home)

    fake_gw = _FakeGateway()

    from hermes_station.app import create_app

    app = create_app()
    app.state.gateway = fake_gw
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        await client.post("/admin/api/pilot/backup/download")

    assert fake_gw.stops == 1
    assert fake_gw.starts == 1


async def test_backup_download_skips_missing_files(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """Files not present on disk are simply omitted from the archive."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    hermes_home = fake_data_dir / ".hermes"
    # Only seed config.yaml — no state.db, no memories.
    (hermes_home / "config.yaml").write_text("provider: openrouter\n")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post("/admin/api/pilot/backup/download")

    assert r.status_code == 200
    buf = io.BytesIO(r.content)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        names = tf.getnames()
    assert "config.yaml" in names
    assert "state.db" not in names
    assert ".env" not in names


# ── restore: unauthenticated ──────────────────────────────────────────────────


async def test_backup_restore_401_when_not_authenticated(fake_data_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/admin/api/pilot/backup/restore")

    assert r.status_code == 401


# ── restore: missing file ─────────────────────────────────────────────────────


async def test_backup_restore_missing_file_returns_error(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        # POST without a file.
        r = await client.post(
            "/admin/api/pilot/backup/restore",
            content=b"",
            headers={"content-type": "application/octet-stream"},
        )

    # Missing backup_file field → 400 or error JSON.
    assert r.status_code in (400, 422)


# ── restore: corrupt archive ──────────────────────────────────────────────────


async def test_backup_restore_corrupt_archive_rejected(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/api/pilot/backup/restore",
            files={"backup_file": ("backup.tar.gz", b"not a real archive", "application/gzip")},
        )

    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False
    assert "invalid" in data["error"].lower()


# ── restore: path traversal ───────────────────────────────────────────────────


async def test_backup_restore_path_traversal_rejected(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    # Build an archive with a traversal path.
    bad_archive = _build_valid_archive({"../etc/passwd": b"root:x:0:0:::"})

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/api/pilot/backup/restore",
            files={"backup_file": ("backup.tar.gz", bad_archive, "application/gzip")},
        )

    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False


async def test_backup_restore_absolute_path_rejected(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    bad_archive = _build_valid_archive({"/etc/passwd": b"root:x:0:0:::"})

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/api/pilot/backup/restore",
            files={"backup_file": ("backup.tar.gz", bad_archive, "application/gzip")},
        )

    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False


async def test_backup_restore_non_allowlisted_file_rejected(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    bad_archive = _build_valid_archive({"secrets.txt": b"oh no"})

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/api/pilot/backup/restore",
            files={"backup_file": ("backup.tar.gz", bad_archive, "application/gzip")},
        )

    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False


# ── restore: success ──────────────────────────────────────────────────────────


async def test_backup_restore_success_calls_gateway_stop_and_start(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    hermes_home = fake_data_dir / ".hermes"
    _seed_files(hermes_home)

    good_archive = _build_valid_archive(
        {
            "config.yaml": b"provider: openrouter\n",
            "SOUL.md": b"# Soul\n",
        }
    )

    fake_gw = _FakeGateway()

    from hermes_station.app import create_app

    app = create_app()
    app.state.gateway = fake_gw
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post(
            "/admin/api/pilot/backup/restore",
            files={"backup_file": ("backup.tar.gz", good_archive, "application/gzip")},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert set(data["files"]) == {"config.yaml", "SOUL.md"}

    # Gateway must have been stopped and started.
    assert fake_gw.stops == 1
    assert fake_gw.starts == 1

    # Files must actually have been written.
    assert (hermes_home / "config.yaml").read_text() == "provider: openrouter\n"
    assert (hermes_home / "SOUL.md").read_text() == "# Soul\n"


# ── restore: flag off ─────────────────────────────────────────────────────────


async def test_backup_restore_404_when_flag_off(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.post("/admin/api/pilot/backup/restore")

    assert r.status_code == 404
