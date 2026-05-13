"""Telegram pairing approve/deny/revoke helpers.

Files live at `$HERMES_HOME/pairing/{telegram-approved,telegram-pending}.json`.
Upstream is migrating these to `$HERMES_HOME/platforms/pairing/` — we read the
new path first if it exists (CONTRACT.md §3.2) and writes follow the same
resolution so reads and writes never diverge.

All file writes are mode 0600 and atomic (temp + rename).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


_APPROVED_FILE = "telegram-approved.json"
_PENDING_FILE = "telegram-pending.json"


def _resolve_pairing_file(pairing_dir: Path, name: str) -> Path:
    """Return the first existing of the new platforms-path then the legacy path.

    Reads should prefer the new path so we don't show stale data after an
    upstream migration; writes still target the legacy path (see writers below).
    """
    new_path = pairing_dir.parent / "platforms" / "pairing" / name
    legacy_path = pairing_dir / name
    if new_path.exists():
        return new_path
    return legacy_path


def _resolve_pairing_write(pairing_dir: Path, name: str) -> Path:
    """Where to write a pairing file: prefer the new platforms path if its dir exists."""
    new_dir = pairing_dir.parent / "platforms" / "pairing"
    if new_dir.exists() or (new_dir / name).exists():
        return new_dir / name
    return pairing_dir / name


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    tmp.replace(path)


def _to_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for user_id, info in data.items():
        entry: dict[str, Any] = {"user_id": user_id}
        if isinstance(info, dict):
            entry.update(info)
        out.append(entry)
    return out


def get_pending(pairing_dir: Path) -> list[dict[str, Any]]:
    return _to_list(_load_json(_resolve_pairing_file(pairing_dir, _PENDING_FILE)))


def get_approved(pairing_dir: Path) -> list[dict[str, Any]]:
    return _to_list(_load_json(_resolve_pairing_file(pairing_dir, _APPROVED_FILE)))


def approve(pairing_dir: Path, user_id: str) -> dict[str, Any]:
    """Move a pending entry into the approved file, stamping `approved_at`."""
    user_id = str(user_id).strip()
    if not user_id:
        raise ValueError("user_id is required")

    pending_read = _resolve_pairing_file(pairing_dir, _PENDING_FILE)
    pending_write = _resolve_pairing_write(pairing_dir, _PENDING_FILE)
    approved_write = _resolve_pairing_write(pairing_dir, _APPROVED_FILE)

    pending = _load_json(pending_read)
    if user_id not in pending:
        raise KeyError(f"No pending entry for user_id={user_id}")
    entry = pending.pop(user_id)
    _write_json(pending_write, pending)

    approved = _load_json(_resolve_pairing_file(pairing_dir, _APPROVED_FILE))
    info: dict[str, Any] = dict(entry) if isinstance(entry, dict) else {}
    info.setdefault("approved_at", time.time())
    approved[user_id] = info
    _write_json(approved_write, approved)
    return {"user_id": user_id, **info}


def deny(pairing_dir: Path, user_id: str) -> dict[str, Any]:
    user_id = str(user_id).strip()
    if not user_id:
        raise ValueError("user_id is required")
    pending_read = _resolve_pairing_file(pairing_dir, _PENDING_FILE)
    pending_write = _resolve_pairing_write(pairing_dir, _PENDING_FILE)
    pending = _load_json(pending_read)
    removed = pending.pop(user_id, None)
    _write_json(pending_write, pending)
    return {"user_id": user_id, "removed": removed is not None}


def revoke(pairing_dir: Path, user_id: str) -> dict[str, Any]:
    user_id = str(user_id).strip()
    if not user_id:
        raise ValueError("user_id is required")
    approved_read = _resolve_pairing_file(pairing_dir, _APPROVED_FILE)
    approved_write = _resolve_pairing_write(pairing_dir, _APPROVED_FILE)
    approved = _load_json(approved_read)
    removed = approved.pop(user_id, None)
    _write_json(approved_write, approved)
    return {"user_id": user_id, "removed": removed is not None}
