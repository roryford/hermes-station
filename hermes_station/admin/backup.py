"""Backup and restore handlers for /admin/backup."""

from __future__ import annotations

import io
import logging
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import is_authenticated, require_admin
from hermes_station.config import Paths

logger = logging.getLogger(__name__)

# Files to include in the backup archive (relative to hermes_home).
_INCLUDE_FILES = [
    ".env",
    "config.yaml",
    "state.db",
    "gateway_state.json",
    "SOUL.md",
]


def _checkpoint_db(db_path: Path) -> None:
    """Run WAL checkpoint (TRUNCATE) on state.db if it exists."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to checkpoint state.db before backup", exc_info=True)


def _build_archive(hermes_home: Path) -> bytes:
    """Build an in-memory tar.gz of the included backup files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in _INCLUDE_FILES:
            file_path = hermes_home / name
            if file_path.exists():
                tar.add(str(file_path), arcname=name)
    return buf.getvalue()


async def backup_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    return _templates.TemplateResponse(
        request,
        "admin/backup.html",
        {"active": "backup", "title": "Backup"},
    )


async def backup_download(request: Request) -> Response:
    """POST /admin/api/backup/download — stream a tar.gz of hermes_home."""
    if not is_authenticated(request):
        from starlette.responses import JSONResponse

        return JSONResponse({"error": "unauthorized"}, status_code=401)

    paths: Paths = request.app.state.paths
    gateway = getattr(request.app.state, "gateway", None)

    # 1. Stop the gateway.
    if gateway is not None:
        await gateway.stop()

    try:
        # 2. Checkpoint state.db.
        _checkpoint_db(paths.hermes_home / "state.db")

        # 3. Build archive.
        archive_bytes = _build_archive(paths.hermes_home)
    finally:
        # 4. Restart the gateway regardless of errors.
        if gateway is not None:
            await gateway.start()

    # 5. Stream the archive.
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"hermes-station-backup-{timestamp}.tar.gz"

    def _iter() -> bytes:
        yield archive_bytes

    return StreamingResponse(
        _iter(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def backup_restore(request: Request) -> Response:
    """POST /admin/api/backup/restore — upload a tar.gz and restore hermes_home."""
    if not is_authenticated(request):
        from starlette.responses import JSONResponse

        return JSONResponse({"error": "unauthorized"}, status_code=401)

    paths: Paths = request.app.state.paths
    gateway = getattr(request.app.state, "gateway", None)

    # Parse the multipart upload.
    form = await request.form()
    backup_file: UploadFile | None = form.get("backup_file")  # type: ignore[assignment]
    if backup_file is None or not getattr(backup_file, "filename", None):
        return _templates.TemplateResponse(
            request,
            "admin/_backup_result.html",
            {"success": False, "message": "No file uploaded."},
            status_code=400,
        )

    raw = await backup_file.read()

    # 1. Validate it's a valid tar.gz.
    try:
        buf = io.BytesIO(raw)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            members = tar.getmembers()
    except Exception as exc:  # noqa: BLE001
        return _templates.TemplateResponse(
            request,
            "admin/_backup_result.html",
            {"success": False, "message": f"Invalid archive: {exc}"},
            status_code=400,
        )

    # 2. Path traversal check.
    for member in members:
        if member.name.startswith("/") or ".." in member.name:
            return _templates.TemplateResponse(
                request,
                "admin/_backup_result.html",
                {
                    "success": False,
                    "message": f"Archive contains unsafe path: {member.name!r}",
                },
                status_code=400,
            )

    member_names = [m.name for m in members]

    # 3. Stop the gateway.
    if gateway is not None:
        await gateway.stop()

    try:
        # 4. Write files to a temp dir, then atomically move each into hermes_home.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            buf.seek(0)
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                tar.extractall(tmpdir, filter="data")  # type: ignore[call-arg]

            for name in member_names:
                src = tmp_path / name
                dest = paths.hermes_home / name
                if src.exists():
                    src.replace(dest)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Restore failed")
        # Restart gateway even on error.
        if gateway is not None:
            await gateway.start()
        return _templates.TemplateResponse(
            request,
            "admin/_backup_result.html",
            {"success": False, "message": f"Restore failed: {exc}"},
            status_code=500,
        )

    # 5. Restart the gateway.
    if gateway is not None:
        await gateway.start()

    return _templates.TemplateResponse(
        request,
        "admin/_backup_result.html",
        {
            "success": True,
            "message": "Restore complete. Gateway restarted.",
            "files": member_names,
        },
    )


def routes() -> list[Route]:
    return [
        Route("/admin/backup", backup_page, methods=["GET"]),
        Route("/admin/api/backup/download", backup_download, methods=["POST"]),
        Route("/admin/api/backup/restore", backup_restore, methods=["POST"]),
    ]
