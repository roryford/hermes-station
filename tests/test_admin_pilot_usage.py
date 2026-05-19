"""Tests for GET /admin/api/pilot/usage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


async def _login(client: httpx.AsyncClient, password: str) -> None:
    r = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert r.status_code == 302, r.text


def _make_state_db(hermes_home: Path) -> Path:
    """Create a minimal state.db with a sessions table and seed data."""
    db = hermes_home / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            source TEXT,
            model TEXT,
            billing_provider TEXT,
            actual_cost_usd REAL,
            estimated_cost_usd REAL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            api_call_count INTEGER
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO sessions
          (created_at, source, model, billing_provider,
           actual_cost_usd, estimated_cost_usd,
           input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
           api_call_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            # Row with actual cost — within last 7 days
            ("2026-05-19T10:00:00", "webui", "gpt-4o", "openai",
             0.08, None, 8000, 2000, 0, 0, 30),
            # Row with estimated cost only — within last 7 days
            ("2026-05-18T08:00:00", "telegram", "claude-3", "anthropic",
             None, 0.04, 4000, 1000, 0, 0, 12),
            # Old row — outside 7 days (25 days ago) but within 30 days
            ("2026-04-25T10:00:00", "webui", "gpt-4o", "openai",
             0.02, None, 2000, 500, 0, 0, 5),
        ],
    )
    conn.commit()
    conn.close()
    return db


# ── unauthenticated ───────────────────────────────────────────────────────────


async def test_usage_401_when_not_authenticated(
    fake_data_dir: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/admin/api/pilot/usage")

    assert r.status_code == 401


# ── flag off ──────────────────────────────────────────────────────────────────


async def test_usage_404_when_flag_off(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.delenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", raising=False)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.get("/admin/api/pilot/usage")

    assert r.status_code == 404


# ── no_db ─────────────────────────────────────────────────────────────────────


async def test_usage_no_db_returns_no_db_true(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """state.db absent → {no_db: true}."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.get("/admin/api/pilot/usage")

    assert r.status_code == 200
    data = r.json()
    assert data["no_db"] is True
    assert data["days"] == 7


# ── with data ─────────────────────────────────────────────────────────────────


async def test_usage_7d_aggregation(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """7d window: 2 rows. Summary cost = 0.08 + 0.04 = 0.12 (approx)."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    hermes_home = fake_data_dir / ".hermes"
    _make_state_db(hermes_home)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.get("/admin/api/pilot/usage?days=7")

    assert r.status_code == 200
    data = r.json()
    assert data["no_db"] is False
    assert data["days"] == 7

    s = data["summary"]
    assert pytest.approx(s["total_cost"], abs=1e-6) == 0.12
    assert s["has_estimated"] is True  # one row has estimated only
    assert s["session_count"] == 2
    assert s["api_calls"] == 42  # 30 + 12

    # By channel: webui and telegram
    ch_names = {c["source"] for c in data["channels"]}
    assert "webui" in ch_names
    assert "telegram" in ch_names

    # By model
    model_names = {m["model"] for m in data["models"]}
    assert "gpt-4o" in model_names
    assert "claude-3" in model_names


async def test_usage_30d_includes_old_row(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """30d window: all 3 rows included."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    hermes_home = fake_data_dir / ".hermes"
    _make_state_db(hermes_home)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.get("/admin/api/pilot/usage?days=30")

    assert r.status_code == 200
    data = r.json()
    s = data["summary"]
    assert s["session_count"] == 3
    assert pytest.approx(s["total_cost"], abs=1e-6) == 0.14


async def test_usage_invalid_days_defaults_to_7(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r = await client.get("/admin/api/pilot/usage?days=999")

    assert r.status_code == 200
    assert r.json()["days"] == 7


async def test_usage_cache_hit(
    fake_data_dir: Path, admin_password: str, monkeypatch
) -> None:
    """Second identical request within 60s returns cached payload."""
    monkeypatch.setenv("HERMES_STATION_PILOT_ADMIN_EXTENSION", "1")
    hermes_home = fake_data_dir / ".hermes"
    _make_state_db(hermes_home)

    from hermes_station.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        r1 = await client.get("/admin/api/pilot/usage?days=7")
        r2 = await client.get("/admin/api/pilot/usage?days=7")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both should return the same total cost.
    assert r1.json()["summary"]["total_cost"] == r2.json()["summary"]["total_cost"]
