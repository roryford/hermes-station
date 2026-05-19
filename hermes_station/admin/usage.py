"""Usage / cost dashboard for /admin/usage.

Reads token and cost telemetry from state.db (sessions table) using
asyncio.to_thread so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import is_authenticated, require_admin

logger = logging.getLogger(__name__)

_CACHE_TTL = 60  # seconds


# ──────────────────────────────────────────────────────────── DB helpers


def _db_path(request: Request) -> Path:
    return Path(request.app.state.paths.hermes_home) / "state.db"


def _effective_cost(row: sqlite3.Row) -> float:
    """Return the best cost estimate for a row."""
    actual = row["actual_cost_usd"]
    if actual is not None:
        return float(actual)
    estimated = row["estimated_cost_usd"]
    return float(estimated) if estimated is not None else 0.0


def _query_usage(db_file: Path, days: int) -> dict[str, Any]:
    """Run all aggregation queries synchronously (called via asyncio.to_thread)."""
    if not db_file.exists():
        return {"no_db": True}

    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()

        # ── summary ───────────────────────────────────────────────────
        cur.execute(
            """
            SELECT
                SUM(CASE WHEN actual_cost_usd IS NOT NULL THEN actual_cost_usd
                         ELSE COALESCE(estimated_cost_usd, 0) END) AS total_cost,
                SUM(COALESCE(input_tokens, 0))       AS input_tokens,
                SUM(COALESCE(output_tokens, 0))      AS output_tokens,
                SUM(COALESCE(cache_read_tokens, 0))  AS cache_read_tokens,
                SUM(COALESCE(cache_write_tokens, 0)) AS cache_write_tokens,
                SUM(COALESCE(api_call_count, 0))     AS api_calls,
                COUNT(*)                              AS session_count,
                SUM(CASE WHEN actual_cost_usd IS NULL AND estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS estimated_rows
            FROM sessions
            WHERE datetime(created_at) >= datetime('now', :window)
            """,
            {"window": f"-{days} days"},
        )
        summary_row = cur.fetchone()

        summary = {
            "total_cost": float(summary_row["total_cost"] or 0),
            "input_tokens": int(summary_row["input_tokens"] or 0),
            "output_tokens": int(summary_row["output_tokens"] or 0),
            "cache_read_tokens": int(summary_row["cache_read_tokens"] or 0),
            "cache_write_tokens": int(summary_row["cache_write_tokens"] or 0),
            "api_calls": int(summary_row["api_calls"] or 0),
            "session_count": int(summary_row["session_count"] or 0),
            "has_estimated": int(summary_row["estimated_rows"] or 0) > 0,
        }

        # ── by channel ────────────────────────────────────────────────
        cur.execute(
            """
            SELECT
                COALESCE(source, 'unknown') AS source,
                SUM(CASE WHEN actual_cost_usd IS NOT NULL THEN actual_cost_usd
                         ELSE COALESCE(estimated_cost_usd, 0) END) AS cost,
                SUM(CASE WHEN actual_cost_usd IS NULL AND estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS estimated_count,
                SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)
                    + COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0)) AS total_tokens,
                SUM(COALESCE(api_call_count, 0)) AS api_calls,
                COUNT(*) AS sessions
            FROM sessions
            WHERE datetime(created_at) >= datetime('now', :window)
            GROUP BY source
            ORDER BY cost DESC
            """,
            {"window": f"-{days} days"},
        )
        channels = [
            {
                "source": r["source"],
                "cost": float(r["cost"] or 0),
                "estimated_count": int(r["estimated_count"] or 0),
                "total_tokens": int(r["total_tokens"] or 0),
                "api_calls": int(r["api_calls"] or 0),
                "sessions": int(r["sessions"] or 0),
            }
            for r in cur.fetchall()
        ]

        # ── by model ──────────────────────────────────────────────────
        cur.execute(
            """
            SELECT
                COALESCE(model, 'unknown') AS model,
                COALESCE(billing_provider, 'unknown') AS billing_provider,
                SUM(CASE WHEN actual_cost_usd IS NOT NULL THEN actual_cost_usd
                         ELSE COALESCE(estimated_cost_usd, 0) END) AS cost,
                SUM(CASE WHEN actual_cost_usd IS NULL AND estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS estimated_count,
                SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)
                    + COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0)) AS total_tokens,
                COUNT(*) AS sessions
            FROM sessions
            WHERE datetime(created_at) >= datetime('now', :window)
            GROUP BY model, billing_provider
            ORDER BY cost DESC
            """,
            {"window": f"-{days} days"},
        )
        models = [
            {
                "model": r["model"],
                "billing_provider": r["billing_provider"],
                "cost": float(r["cost"] or 0),
                "estimated_count": int(r["estimated_count"] or 0),
                "total_tokens": int(r["total_tokens"] or 0),
                "sessions": int(r["sessions"] or 0),
            }
            for r in cur.fetchall()
        ]

        # Max cost values for optional bar rendering (avoid division by zero).
        max_channel_cost = max((r["cost"] for r in channels), default=0.0)
        max_model_cost = max((r["cost"] for r in models), default=0.0)

        return {
            "no_db": False,
            "summary": summary,
            "channels": channels,
            "models": models,
            "max_channel_cost": max_channel_cost,
            "max_model_cost": max_model_cost,
        }
    finally:
        con.close()


# ──────────────────────────────────────────────────────────── cache helpers


def _get_cached(request: Request, days: int) -> tuple[dict[str, Any] | None, float]:
    cache = getattr(request.app.state, "_usage_cache", None)
    if cache is None or cache.get("days") != days:
        return None, 0.0
    return cache.get("data"), cache.get("ts", 0.0)


def _set_cache(request: Request, days: int, data: dict[str, Any]) -> None:
    request.app.state._usage_cache = {"data": data, "ts": time.monotonic(), "days": days}


# ──────────────────────────────────────────────────────────── view helpers


def _cost_str(cost: float, has_estimated: bool) -> str:
    prefix = "~" if has_estimated else ""
    return f"{prefix}${cost:.4f}"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _bar_pct(cost: float, max_cost: float) -> int:
    if max_cost <= 0:
        return 0
    return max(1, int(cost / max_cost * 100))


def _enrich_channels(channels: list[dict[str, Any]], max_cost: float) -> list[dict[str, Any]]:
    out = []
    for r in channels:
        out.append(
            {
                **r,
                "cost_str": _cost_str(r["cost"], r["estimated_count"] > 0),
                "tokens_str": _fmt_tokens(r["total_tokens"]),
                "bar_pct": _bar_pct(r["cost"], max_cost),
            }
        )
    return out


def _enrich_models(models: list[dict[str, Any]], max_cost: float) -> list[dict[str, Any]]:
    out = []
    for r in models:
        out.append(
            {
                **r,
                "cost_str": _cost_str(r["cost"], r["estimated_count"] > 0),
                "tokens_str": _fmt_tokens(r["total_tokens"]),
                "bar_pct": _bar_pct(r["cost"], max_cost),
            }
        )
    return out


# ──────────────────────────────────────────────────────────── handlers


def _parse_days(request: Request) -> int:
    try:
        days = int(request.query_params.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    return days if days in (7, 30) else 7


async def usage_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    days = _parse_days(request)
    return _templates.TemplateResponse(
        request,
        "admin/usage.html",
        {"active": "usage", "title": "Usage", "days": days},
    )


async def usage_data(request: Request) -> Response:
    """GET /admin/_partial/usage/data — returns the data fragment (htmx swap target)."""
    if not is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    days = _parse_days(request)
    nocache = request.query_params.get("nocache") == "true"

    if nocache:
        data, ts = None, 0.0
    else:
        data, ts = _get_cached(request, days)
    age = time.monotonic() - ts if ts else None
    if data is None or age is None or age >= _CACHE_TTL:
        db_file = _db_path(request)
        data = await asyncio.to_thread(_query_usage, db_file, days)
        _set_cache(request, days, data)
        age = 0.0

    ctx: dict[str, Any] = {"days": days, "cache_age": int(age)}

    if data.get("no_db"):
        ctx["no_db"] = True
    else:
        summary = data["summary"]
        ctx.update(
            {
                "no_db": False,
                "summary": {
                    **summary,
                    "total_cost_str": _cost_str(summary["total_cost"], summary["has_estimated"]),
                    "input_tokens_str": _fmt_tokens(summary["input_tokens"]),
                    "output_tokens_str": _fmt_tokens(summary["output_tokens"]),
                    "cache_read_tokens_str": _fmt_tokens(summary["cache_read_tokens"]),
                    "cache_write_tokens_str": _fmt_tokens(summary["cache_write_tokens"]),
                    "total_tokens_str": _fmt_tokens(
                        summary["input_tokens"]
                        + summary["output_tokens"]
                        + summary["cache_read_tokens"]
                        + summary["cache_write_tokens"]
                    ),
                },
                "channels": _enrich_channels(data["channels"], data["max_channel_cost"]),
                "models": _enrich_models(data["models"], data["max_model_cost"]),
                "has_estimated": summary["has_estimated"],
            }
        )

    return _templates.TemplateResponse(request, "admin/_usage_data.html", ctx)


def routes() -> list[Route]:
    return [
        Route("/admin/usage", usage_page, methods=["GET"]),
        Route("/admin/_partial/usage/data", usage_data, methods=["GET"]),
    ]
