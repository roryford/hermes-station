"""Smoke test runner for /admin/smoketest."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from hermes_station.admin._templates import templates as _templates
from hermes_station.admin.auth import is_authenticated, require_admin
from hermes_station.config import Paths, load_env_file, load_yaml_config


_TIMEOUT = 6.0  # seconds per HTTP test


def _pick_env_key(env: dict[str, str], names: list[str] | tuple[str, ...]) -> str:
    """Return the first non-empty value found in env or os.environ for any of names."""
    for n in names:
        v = (env.get(n) or os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def _probe_storage(home: str) -> None:
    probe = Path(home) / ".smoketest_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


async def _test_storage(paths: Paths) -> dict[str, Any]:
    try:
        await asyncio.to_thread(_probe_storage, str(paths.home))
        return {
            "name": "storage",
            "label": "Storage",
            "status": "pass",
            "detail": f"{paths.home} is writable.",
            "fix": "",
        }
    except OSError as exc:
        return {
            "name": "storage",
            "label": "Storage",
            "status": "fail",
            "detail": str(exc),
            "fix": "Check that a Railway volume is mounted at /data.",
        }


async def _test_provider(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    # Deferred to avoid a circular import: provider imports config which imports admin helpers.
    from hermes_station.admin.provider import provider_env_var_names

    model = config.get("model") or {}
    provider = str(model.get("provider") or "").strip().lower()
    if not provider:
        return {
            "name": "provider",
            "label": "Provider",
            "status": "skip",
            "detail": "No provider configured.",
            "fix": "Add a provider key in Settings.",
        }

    names = provider_env_var_names(provider)
    key = _pick_env_key(env, names)
    if not key:
        return {
            "name": "provider",
            "label": "Provider",
            "status": "fail",
            "detail": f"No credential found for {provider!r}.",
            "fix": "Add the API key in Settings.",
        }

    # Providers with a cheap listing endpoint to validate the key.
    check_url: str | None = None
    headers: dict[str, str] = {}
    if provider == "openrouter":
        check_url = "https://openrouter.ai/api/v1/models"
        headers = {"Authorization": f"Bearer {key}"}
    elif provider == "anthropic":
        check_url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif provider == "openai":
        check_url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {key}"}
    # copilot / custom: skip HTTP check
    if check_url is None:
        return {
            "name": "provider",
            "label": "Provider",
            "status": "pass",
            "detail": (f"{provider} credential is present (HTTP check not available for this provider)."),
            "fix": "",
        }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(check_url, headers=headers)
        if resp.status_code == 200:
            return {
                "name": "provider",
                "label": "Provider",
                "status": "pass",
                "detail": f"{provider} API is reachable (HTTP {resp.status_code}).",
                "fix": "",
            }
        return {
            "name": "provider",
            "label": "Provider",
            "status": "fail",
            "detail": f"{provider} API returned HTTP {resp.status_code}.",
            "fix": "Check your API key in Settings — it may be invalid or expired.",
        }
    except httpx.TimeoutException:
        return {
            "name": "provider",
            "label": "Provider",
            "status": "fail",
            "detail": f"{provider} API timed out after {_TIMEOUT:.0f}s.",
            "fix": "Check your network connectivity or try again.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "provider",
            "label": "Provider",
            "status": "fail",
            "detail": str(exc),
            "fix": "Check logs for details.",
        }


async def _test_gateway(gateway: Any, config: dict[str, Any]) -> dict[str, Any]:
    model = config.get("model") or {}
    provider = str(model.get("provider") or "").strip()
    if not provider:
        return {
            "name": "gateway",
            "label": "Gateway",
            "status": "skip",
            "detail": "No provider configured — gateway will not start without one.",
            "fix": "",
        }
    if gateway is None:
        return {
            "name": "gateway",
            "label": "Gateway",
            "status": "fail",
            "detail": "Gateway supervisor not initialised.",
            "fix": "Check logs.",
        }
    try:
        state = gateway.gateway_state
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "gateway",
            "label": "Gateway",
            "status": "fail",
            "detail": str(exc),
            "fix": "Check logs.",
        }
    if state == "running":
        return {
            "name": "gateway",
            "label": "Gateway",
            "status": "pass",
            "detail": "Gateway is running and connected.",
            "fix": "",
        }
    return {
        "name": "gateway",
        "label": "Gateway",
        "status": "fail",
        "detail": f"Gateway state: {state!r}.",
        "fix": "Start the gateway in the Dashboard → Supervisors panel.",
    }


async def _test_github_mcp(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    mcp = config.get("mcp_servers") or {}
    gh = mcp.get("github") if isinstance(mcp, dict) else None
    if not (isinstance(gh, dict) and gh.get("enabled")):
        return {
            "name": "github_mcp",
            "label": "GitHub MCP",
            "status": "skip",
            "detail": "GitHub MCP is not enabled.",
            "fix": "",
        }
    token = (
        env.get("GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or env.get("GH_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    ).strip()
    if not token:
        return {
            "name": "github_mcp",
            "label": "GitHub MCP",
            "status": "fail",
            "detail": "GITHUB_TOKEN / GH_TOKEN is not set.",
            "fix": "Add GITHUB_TOKEN in Settings → Secrets.",
        }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code == 200:
            login = resp.json().get("login", "?")
            return {
                "name": "github_mcp",
                "label": "GitHub MCP",
                "status": "pass",
                "detail": f"Authenticated as @{login}.",
                "fix": "",
            }
        return {
            "name": "github_mcp",
            "label": "GitHub MCP",
            "status": "fail",
            "detail": f"GitHub API returned HTTP {resp.status_code}.",
            "fix": "Check GITHUB_TOKEN in Settings → Secrets — it may be expired.",
        }
    except httpx.TimeoutException:
        return {
            "name": "github_mcp",
            "label": "GitHub MCP",
            "status": "fail",
            "detail": "GitHub API timed out.",
            "fix": "Check network connectivity.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "github_mcp",
            "label": "GitHub MCP",
            "status": "fail",
            "detail": str(exc),
            "fix": "Check logs for details.",
        }


# Backend → env var name (empty = no key required).
# Keep in sync with readiness._WEB_SEARCH_KEYS.
_WEB_SEARCH_ENV: dict[str, str] = {
    "brave": "BRAVE_API_KEY",
    "brave-free": "BRAVE_SEARCH_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "google": "GOOGLE_CSE_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "exa": "EXA_API_KEY",
    "parallel": "PARALLEL_API_KEY",
    "searxng": "SEARXNG_URL",
    "ddgs": "",
}


def _r(status: str, detail: str, fix: str = "") -> dict[str, Any]:
    return {"name": "web_search", "label": "Web search", "status": status, "detail": detail, "fix": fix}


async def _test_web_search(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    web = config.get("web") or {}
    backend = str(web.get("search_backend") or "").strip().lower()
    if not backend:
        return _r("skip", "No search backend configured.")
    if backend not in _WEB_SEARCH_ENV:
        return _r("fail", f"Unknown search backend {backend!r}.", "Check web.search_backend in config.yaml.")

    env_key = _WEB_SEARCH_ENV[backend]

    # ddgs needs no API key — if it's configured it's ready.
    if not env_key:
        return _r("pass", "DuckDuckGo search requires no API key.")

    key = (env.get(env_key) or os.environ.get(env_key) or "").strip()
    if not key:
        return _r("fail", f"{env_key} is not set.", f"Add {env_key} in Settings → Secrets.")

    # Backends with a live HTTP check.
    if backend in ("brave", "brave-free"):
        return await _check_brave(key, env_key)
    if backend == "tavily":
        return await _check_tavily(key)

    # All other backends: key is present — treat as pass.
    return _r("pass", f"{backend} credential is present ({env_key} is set).")


async def _check_brave(key: str, env_key: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "test", "count": "1"},
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
            )
        if resp.status_code == 200:
            return _r("pass", "Brave Search API is reachable.")
        return _r(
            "fail",
            f"Brave Search returned HTTP {resp.status_code}.",
            f"Check {env_key} in Settings → Secrets — it may be invalid.",
        )
    except httpx.TimeoutException:
        return _r("fail", "Brave Search timed out.", "Check network connectivity.")
    except Exception as exc:  # noqa: BLE001
        return _r("fail", str(exc), "Check logs for details.")


async def _check_tavily(key: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"query": "test", "max_results": 1},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        if resp.status_code == 200:
            return _r("pass", "Tavily Search API is reachable.")
        return _r(
            "fail",
            f"Tavily Search returned HTTP {resp.status_code}.",
            "Check TAVILY_API_KEY in Settings → Secrets — it may be invalid.",
        )
    except httpx.TimeoutException:
        return _r("fail", "Tavily Search timed out.", "Check network connectivity.")
    except Exception as exc:  # noqa: BLE001
        return _r("fail", str(exc), "Check logs for details.")


# Browser backend env vars in priority order (highest first).
_BROWSER_BACKENDS: list[tuple[str, list[str]]] = [
    ("camofox", ["CAMOFOX_URL"]),
    ("browserbase", ["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"]),
    ("browser_use", ["BROWSER_USE_API_KEY"]),
    ("steel", ["STEEL_API_KEY"]),
]


def _rb(status: str, detail: str, fix: str = "") -> dict[str, Any]:
    return {
        "name": "browser_backend",
        "label": "Browser backend",
        "status": status,
        "detail": detail,
        "fix": fix,
    }


async def _test_browser_backend(env: dict[str, str]) -> dict[str, Any]:
    # Determine which backend (if any) is configured.
    for backend, keys in _BROWSER_BACKENDS:
        values = {k: (env.get(k) or os.environ.get(k) or "").strip() for k in keys}
        if not all(values.values()):
            continue

        if backend == "camofox":
            url = values["CAMOFOX_URL"].rstrip("/")
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.get(f"{url}/health")
                    if resp.status_code == 404:
                        resp = await client.get(url)
                if resp.is_success:
                    return _rb("pass", f"Camofox reachable at {url}.")
                return _rb(
                    "fail",
                    f"Camofox returned HTTP {resp.status_code}.",
                    "Check CAMOFOX_URL in Settings — make sure the service is running.",
                )
            except Exception as exc:  # noqa: BLE001
                return _rb(
                    "fail",
                    str(exc),
                    "Check CAMOFOX_URL in Settings — make sure the service is running.",
                )

        if backend == "browserbase":
            key = values["BROWSERBASE_API_KEY"]
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.get(
                        "https://www.browserbase.com/v1/sessions",
                        headers={"X-BB-API-Key": key},
                    )
                if resp.status_code == 200:
                    return _rb("pass", "Browserbase API is reachable.")
                if resp.status_code == 401:
                    return _rb(
                        "fail",
                        "BROWSERBASE_API_KEY is invalid.",
                        "Check BROWSERBASE_API_KEY in Settings → Secrets.",
                    )
                return _rb(
                    "fail",
                    f"Browserbase returned HTTP {resp.status_code}.",
                    "Check BROWSERBASE_API_KEY in Settings → Secrets.",
                )
            except Exception as exc:  # noqa: BLE001
                return _rb(
                    "fail",
                    str(exc),
                    "Check network connectivity and BROWSERBASE_API_KEY.",
                )

        if backend == "browser_use":
            return _rb("pass", "Browser Use credential is present.")

        if backend == "steel":
            return _rb("pass", "Steel credential is present.")

    return _rb("skip", "No browser backend configured.")


async def run_all_tests(request: Request) -> list[dict[str, Any]]:
    paths: Paths = request.app.state.paths
    config = load_yaml_config(paths.config_path)
    env = load_env_file(paths.env_path)
    gateway = getattr(request.app.state, "gateway", None)
    results = await asyncio.gather(
        _test_storage(paths),
        _test_provider(config, env),
        _test_gateway(gateway, config),
        _test_github_mcp(config, env),
        _test_web_search(config, env),
        _test_browser_backend(env),
    )
    return list(results)


async def smoketest_page(request: Request) -> Response:
    guard = require_admin(request)
    if guard is not None:
        return guard
    return _templates.TemplateResponse(
        request,
        "admin/smoketest.html",
        {"active": "smoketest", "title": "Smoke tests"},
    )


async def smoketest_run(request: Request) -> Response:
    if not is_authenticated(request):
        from starlette.responses import JSONResponse

        return JSONResponse({"error": "unauthorized"}, status_code=401)
    results = await run_all_tests(request)
    return _templates.TemplateResponse(
        request,
        "admin/_smoketest_results.html",
        {"results": results},
    )


def routes() -> list[Route]:
    return [
        Route("/admin/smoketest", smoketest_page, methods=["GET"]),
        Route("/admin/_partial/smoketest/run", smoketest_run, methods=["POST"]),
    ]
