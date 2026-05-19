"""Tests for the /admin/usage page and helpers."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from starlette.applications import Starlette

from hermes_station.admin.usage import (
    _bar_pct,
    _cost_str,
    _fmt_tokens,
    _query_usage,
    routes as usage_routes,
)
from hermes_station.admin.routes import admin_routes


# ──────────────────────────────────────────────────── helpers


def _build_app(hermes_home: Path) -> Starlette:
    """Minimal test app: login + usage routes, no proxy/supervisors."""
    app = Starlette(routes=[*admin_routes(), *usage_routes()])
    app.state.paths = SimpleNamespace(hermes_home=str(hermes_home))
    return app


async def _login(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post("/admin/login", data={"password": password}, follow_redirects=False)
    assert response.status_code == 302, response.text


def _make_state_db(hermes_home: Path) -> Path:
    """Create a minimal state.db with a sessions table."""
    db_path = hermes_home / "state.db"
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            model TEXT,
            billing_provider TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            api_call_count INTEGER,
            actual_cost_usd REAL,
            estimated_cost_usd REAL,
            cost_status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    con.commit()
    con.close()
    return db_path


def _insert_session(
    db_path: Path,
    source: str = "cli",
    model: str = "gpt-4o",
    billing_provider: str = "openai",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    api_call_count: int = 1,
    actual_cost_usd: float | None = 0.005,
    estimated_cost_usd: float | None = None,
    cost_status: str = "actual",
    created_at: str = "2026-05-19 10:00:00",
) -> None:
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        INSERT INTO sessions
            (source, model, billing_provider,
             input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
             api_call_count, actual_cost_usd, estimated_cost_usd, cost_status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source,
            model,
            billing_provider,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            api_call_count,
            actual_cost_usd,
            estimated_cost_usd,
            cost_status,
            created_at,
        ),
    )
    con.commit()
    con.close()


# ──────────────────────────────────────────────────── unit helpers


def test_cost_str_actual() -> None:
    assert _cost_str(0.1234, False) == "$0.1234"


def test_cost_str_estimated_prefix() -> None:
    assert _cost_str(0.1234, True) == "~$0.1234"


def test_cost_str_zero() -> None:
    assert _cost_str(0.0, False) == "$0.0000"


def test_fmt_tokens_small() -> None:
    assert _fmt_tokens(999) == "999"


def test_fmt_tokens_thousands() -> None:
    result = _fmt_tokens(1500)
    assert result.endswith("K")


def test_fmt_tokens_millions() -> None:
    result = _fmt_tokens(2_500_000)
    assert result.endswith("M")


def test_bar_pct_zero_max() -> None:
    assert _bar_pct(5.0, 0.0) == 0


def test_bar_pct_full() -> None:
    assert _bar_pct(10.0, 10.0) == 100


def test_bar_pct_half() -> None:
    assert _bar_pct(5.0, 10.0) == 50


# ──────────────────────────────────────────────────── _query_usage


def test_query_usage_no_db(tmp_path: Path) -> None:
    """When state.db doesn't exist, returns no_db=True."""
    result = _query_usage(tmp_path / "state.db", 7)
    assert result["no_db"] is True


def test_query_usage_empty_db(tmp_path: Path) -> None:
    """An empty sessions table returns zero-sum data."""
    db_path = _make_state_db(tmp_path)
    result = _query_usage(db_path, 7)
    assert result["no_db"] is False
    assert result["summary"]["total_cost"] == 0.0
    assert result["summary"]["session_count"] == 0
    assert result["channels"] == []
    assert result["models"] == []


def test_query_usage_single_session(tmp_path: Path) -> None:
    """A single session within the window is aggregated correctly."""
    db_path = _make_state_db(tmp_path)
    _insert_session(
        db_path,
        source="telegram",
        model="claude-3-5-sonnet",
        billing_provider="anthropic",
        input_tokens=200,
        output_tokens=100,
        api_call_count=2,
        actual_cost_usd=0.01,
        created_at="2026-05-19 12:00:00",
    )
    result = _query_usage(db_path, 7)
    assert result["no_db"] is False
    summary = result["summary"]
    assert summary["total_cost"] == pytest.approx(0.01)
    assert summary["input_tokens"] == 200
    assert summary["output_tokens"] == 100
    assert summary["api_calls"] == 2
    assert summary["session_count"] == 1
    assert len(result["channels"]) == 1
    assert result["channels"][0]["source"] == "telegram"
    assert len(result["models"]) == 1
    assert result["models"][0]["model"] == "claude-3-5-sonnet"


def test_query_usage_estimated_fallback(tmp_path: Path) -> None:
    """When actual_cost is NULL, estimated_cost is used and has_estimated is True."""
    db_path = _make_state_db(tmp_path)
    _insert_session(
        db_path,
        actual_cost_usd=None,
        estimated_cost_usd=0.002,
        cost_status="estimated",
        created_at="2026-05-19 12:00:00",
    )
    result = _query_usage(db_path, 7)
    assert result["summary"]["total_cost"] == pytest.approx(0.002)
    assert result["summary"]["has_estimated"] is True


def test_query_usage_sorts_by_cost_desc(tmp_path: Path) -> None:
    """Channels and models are returned sorted by cost descending."""
    db_path = _make_state_db(tmp_path)
    _insert_session(db_path, source="cli", actual_cost_usd=0.001, created_at="2026-05-19 12:00:00")
    _insert_session(db_path, source="telegram", actual_cost_usd=0.010, created_at="2026-05-19 12:01:00")
    _insert_session(db_path, source="discord", actual_cost_usd=0.005, created_at="2026-05-19 12:02:00")
    result = _query_usage(db_path, 7)
    costs = [r["cost"] for r in result["channels"]]
    assert costs == sorted(costs, reverse=True)


def test_query_usage_window_excludes_old_rows(tmp_path: Path) -> None:
    """Sessions outside the day window are excluded."""
    db_path = _make_state_db(tmp_path)
    # Insert one recent and one ancient session.
    _insert_session(db_path, actual_cost_usd=0.01, created_at="2026-05-19 12:00:00")  # recent
    _insert_session(db_path, actual_cost_usd=99.0, created_at="2020-01-01 00:00:00")  # ancient
    result = _query_usage(db_path, 7)
    assert result["summary"]["total_cost"] == pytest.approx(0.01)
    assert result["summary"]["session_count"] == 1


def test_query_usage_max_cost_values(tmp_path: Path) -> None:
    """max_channel_cost and max_model_cost reflect the top-cost rows."""
    db_path = _make_state_db(tmp_path)
    _insert_session(db_path, source="cli", actual_cost_usd=0.10, created_at="2026-05-19 12:00:00")
    _insert_session(db_path, source="telegram", actual_cost_usd=0.05, created_at="2026-05-19 12:01:00")
    result = _query_usage(db_path, 7)
    assert result["max_channel_cost"] == pytest.approx(0.10)


# ──────────────────────────────────────────────────── HTTP endpoints


async def test_usage_page_returns_200_without_db(fake_data_dir: Path, admin_password: str) -> None:
    """The page renders (200) even when state.db is absent."""
    hermes_home = fake_data_dir / ".hermes"
    assert not (hermes_home / "state.db").exists()
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/usage")
    assert resp.status_code == 200
    assert "Usage" in resp.text


async def test_usage_page_requires_auth(fake_data_dir: Path, admin_password: str) -> None:
    hermes_home = fake_data_dir / ".hermes"
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/usage", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"


async def test_usage_data_unauthenticated_returns_401(fake_data_dir: Path, admin_password: str) -> None:
    hermes_home = fake_data_dir / ".hermes"
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/_partial/usage/data")
    assert resp.status_code == 401


async def test_usage_data_no_db_shows_placeholder(fake_data_dir: Path, admin_password: str) -> None:
    """When state.db is absent, the fragment shows the 'no data yet' placeholder."""
    hermes_home = fake_data_dir / ".hermes"
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/_partial/usage/data?days=7")
    assert resp.status_code == 200
    assert "No data yet" in resp.text or "No usage data" in resp.text


async def test_usage_data_with_sessions_renders_tables(fake_data_dir: Path, admin_password: str) -> None:
    """When state.db has sessions, the fragment renders cost and token tables."""
    hermes_home = fake_data_dir / ".hermes"
    db_path = _make_state_db(hermes_home)
    _insert_session(
        db_path,
        source="webui",
        model="gpt-4o",
        billing_provider="openai",
        actual_cost_usd=0.0123,
        created_at="2026-05-19 10:00:00",
    )
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/_partial/usage/data?days=7")
    assert resp.status_code == 200
    assert "webui" in resp.text
    assert "gpt-4o" in resp.text
    assert "0.0123" in resp.text


async def test_usage_data_cache_prevents_second_query(fake_data_dir: Path, admin_password: str) -> None:
    """A second request within TTL hits the cache, not the DB."""
    hermes_home = fake_data_dir / ".hermes"
    _make_state_db(hermes_home)
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    with patch("hermes_station.admin.usage.asyncio.to_thread") as mock_thread:
        # Seed a fake cached result so the first call doesn't actually need the DB.
        first_data = {"no_db": False, "summary": {"total_cost": 0.0, "input_tokens": 0,
            "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "api_calls": 0, "session_count": 0, "has_estimated": False},
            "channels": [], "models": [], "max_channel_cost": 0.0, "max_model_cost": 0.0}
        mock_thread.return_value = first_data
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            await client.get("/admin/_partial/usage/data?days=7")
            first_call_count = mock_thread.call_count
            await client.get("/admin/_partial/usage/data?days=7")
            second_call_count = mock_thread.call_count

    # Cache hit — no second call to asyncio.to_thread.
    assert second_call_count == first_call_count


async def test_usage_data_cache_expires(
    fake_data_dir: Path, admin_password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After TTL expires the cache is refreshed."""
    hermes_home = fake_data_dir / ".hermes"
    _make_state_db(hermes_home)
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    fake_data = {"no_db": False, "summary": {"total_cost": 0.0, "input_tokens": 0,
        "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "api_calls": 0, "session_count": 0, "has_estimated": False},
        "channels": [], "models": [], "max_channel_cost": 0.0, "max_model_cost": 0.0}
    with patch("hermes_station.admin.usage.asyncio.to_thread", return_value=fake_data) as mock_thread:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            await client.get("/admin/_partial/usage/data?days=7")
            count_after_first = mock_thread.call_count

            # Back-date cache timestamp to simulate expiry.
            app.state._usage_cache["ts"] = time.monotonic() - 120

            await client.get("/admin/_partial/usage/data?days=7")
            count_after_second = mock_thread.call_count

    assert count_after_second > count_after_first


async def test_usage_data_invalid_days_param_does_not_crash(
    fake_data_dir: Path, admin_password: str
) -> None:
    """A non-integer days param falls back to the default (7) instead of 500."""
    hermes_home = fake_data_dir / ".hermes"
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/_partial/usage/data?days=garbage")
    assert resp.status_code == 200


async def test_usage_data_nocache_bypasses_cache(
    fake_data_dir: Path, admin_password: str
) -> None:
    """nocache=true forces a fresh query even within TTL."""
    hermes_home = fake_data_dir / ".hermes"
    _make_state_db(hermes_home)
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    fake_data = {"no_db": False, "summary": {"total_cost": 0.0, "input_tokens": 0,
        "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "api_calls": 0, "session_count": 0, "has_estimated": False},
        "channels": [], "models": [], "max_channel_cost": 0.0, "max_model_cost": 0.0}
    with patch("hermes_station.admin.usage.asyncio.to_thread", return_value=fake_data) as mock_thread:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, admin_password)
            await client.get("/admin/_partial/usage/data?days=7")
            count_after_first = mock_thread.call_count
            await client.get("/admin/_partial/usage/data?days=7&nocache=true")
            count_after_second = mock_thread.call_count
    assert count_after_second > count_after_first


async def test_usage_data_days_30_param(fake_data_dir: Path, admin_password: str) -> None:
    """days=30 query param is accepted without error, and the summary label says 30."""
    hermes_home = fake_data_dir / ".hermes"
    db_path = _make_state_db(hermes_home)
    _insert_session(db_path, actual_cost_usd=0.001, created_at="2026-05-19 10:00:00")
    app = _build_app(hermes_home)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, admin_password)
        resp = await client.get("/admin/_partial/usage/data?days=30")
    assert resp.status_code == 200
    assert "30 days" in resp.text
