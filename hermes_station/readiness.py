"""Boot-time capability validation.

Reconciles *intended* capability (from config.yaml) against *actual* readiness
(env vars present, paths writable, etc.) and produces a structured report that
is cached on ``app.state.readiness`` at startup and consumed by ``/health``.

**Semantics**: this is a *boot-time snapshot*, not a live end-to-end health
check.  It answers "was the operator's intended configuration satisfiable at
start-up?" — not "is the downstream service reachable right now?"  Capabilities
like provider credentials and channel tokens are validated once at boot; the
cached result is served until the process restarts.

Default posture is warn-and-continue: missing secrets do NOT block startup;
they just flip a capability to ``ready=false`` with a short reason, which causes
``/health`` to report ``status: "degraded"``.  The image must stay shareable, so
we never abort on missing credentials.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_station.admin.channels import CHANNEL_CATALOG
from hermes_station.admin.provider import (
    PROVIDER_CATALOG,
    provider_env_var_names,
)

logger = logging.getLogger("hermes_station.readiness")


# Placeholder values seen in example configs / boilerplate that we don't
# want to count as "credentials present".
_PLACEHOLDER_TOKENS = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "your-token-here",
        "your_token_here",
        "xxx",
        "todo",
        "<token>",
        "<your-token>",
    }
)


# Web search backends → env var.
_WEB_SEARCH_KEYS: dict[str, str] = {
    "brave": "BRAVE_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "google": "GOOGLE_CSE_API_KEY",
}


@dataclass
class CapabilityRow:
    intended: bool
    ready: bool
    reason: str = ""
    source: str = ""  # "env_file" | "process_env" | "absent" | "" (non-credential caps)
    notes: str = ""  # operator-actionable hint (e.g. provider drift) — surfaced in /health

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"intended": self.intended, "ready": self.ready}
        if self.reason:
            out["reason"] = self.reason
        if self.source:
            out["source"] = self.source
        if self.notes:
            out["notes"] = self.notes
        return out


@dataclass
class Readiness:
    readiness: dict[str, CapabilityRow] = field(default_factory=dict)
    versions: dict[str, Any] = field(default_factory=dict)
    boot_at: str = ""
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "readiness": {k: v.as_dict() for k, v in self.readiness.items()},
            "versions": dict(self.versions),
            "boot_at": self.boot_at,
            "summary": dict(self.summary),
        }

    def any_intended_not_ready(self) -> bool:
        return any(row.intended and not row.ready for row in self.readiness.values())


def _has_value(env_values: dict[str, str], key: str) -> bool:
    """True iff *key* is set (in .env values or os.environ) to a non-placeholder."""
    raw = (env_values.get(key) or os.environ.get(key) or "").strip()
    return bool(raw) and raw.lower() not in _PLACEHOLDER_TOKENS


def _first_present(env_values: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        if _has_value(env_values, k):
            return k
    return ""


def _credential_source(env_values: dict[str, str], *keys: str) -> str:
    """Return where the first non-placeholder credential was found.

    Returns ``"env_file"`` if found in the .env dict, ``"process_env"`` if found
    only in os.environ, or ``"absent"`` if none of the keys have a usable value.
    Checking .env first matters for the OpenRouter case where the same key may
    exist in both process env (Railway-injected) and .env (user-overridden).
    """
    for key in keys:
        raw = (env_values.get(key) or "").strip()
        if raw and raw.lower() not in _PLACEHOLDER_TOKENS:
            return "env_file"
        raw = (os.environ.get(key) or "").strip()
        if raw and raw.lower() not in _PLACEHOLDER_TOKENS:
            return "process_env"
    return "absent"


def _channel_intended(config: dict[str, Any], slug: str) -> bool:
    """Did the user *intend* to use the channel? Look at config.yaml hints.

    config.yaml may contain a `messaging.<slug>` block or a `channels` list.
    On a default install neither is present — we treat that as not-intended.
    """
    messaging = config.get("messaging") if isinstance(config.get("messaging"), dict) else {}
    if messaging and isinstance(messaging.get(slug), dict):
        block = messaging[slug]
        if block.get("enabled") is False:
            return False
        return True
    channels = config.get("channels")
    if isinstance(channels, list) and slug in {str(c).lower() for c in channels}:
        return True
    if isinstance(channels, dict) and slug in {str(k).lower() for k in channels}:
        block = channels.get(slug) or {}
        if isinstance(block, dict) and block.get("enabled") is False:
            return False
        return True
    return False


def _delegation_providers(config: dict[str, Any]) -> list[str]:
    """Return provider IDs referenced by a `delegation:` block, if any."""
    delegation = config.get("delegation")
    out: list[str] = []
    if isinstance(delegation, dict):
        p = str(delegation.get("provider") or "").strip().lower()
        if p:
            out.append(p)
        # Some configs nest providers in a list under `delegations`/`routes`.
        for entry_key in ("delegations", "routes", "fallback"):
            entries = delegation.get(entry_key)
            if isinstance(entries, list):
                for e in entries:
                    if isinstance(e, dict):
                        ep = str(e.get("provider") or "").strip().lower()
                        if ep:
                            out.append(ep)
    return out


def _check_provider(provider: str, env_values: dict[str, str], *, intended: bool) -> CapabilityRow:
    if not provider:
        return CapabilityRow(intended=False, ready=False, reason="no provider")
    if provider not in PROVIDER_CATALOG:
        return CapabilityRow(
            intended=intended,
            ready=False,
            reason=f"unknown provider {provider!r}",
        )
    names = provider_env_var_names(provider)
    source = _credential_source(env_values, *names)
    ok = source != "absent"
    reason = "" if ok else f"missing {' or '.join(names) if names else 'credential'}"
    return CapabilityRow(intended=intended, ready=ok, reason=reason, source=source)


def _check_web_search(config: dict[str, Any], env_values: dict[str, str]) -> CapabilityRow:
    web = config.get("web") if isinstance(config.get("web"), dict) else {}
    backend = str(web.get("search_backend") or "").strip().lower()
    if not backend:
        return CapabilityRow(intended=False, ready=False)
    key = _WEB_SEARCH_KEYS.get(backend)
    if not key:
        return CapabilityRow(intended=True, ready=False, reason=f"unknown web search backend {backend!r}")
    source = _credential_source(env_values, key)
    ok = source != "absent"
    return CapabilityRow(intended=True, ready=ok, reason="" if ok else f"missing {key}", source=source)


def _check_image_gen(config: dict[str, Any], env_values: dict[str, str]) -> CapabilityRow:
    if not _image_gen_intended(config):
        return CapabilityRow(intended=False, ready=False)
    source = _credential_source(env_values, "FAL_KEY")
    ok = source != "absent"
    return CapabilityRow(intended=True, ready=ok, reason="" if ok else "missing FAL_KEY", source=source)


def _check_github(config: dict[str, Any], env_values: dict[str, str]) -> CapabilityRow:
    intended = False
    mcp = config.get("mcp_servers")
    if isinstance(mcp, dict):
        gh = mcp.get("github")
        if isinstance(gh, dict) and bool(gh.get("enabled")):
            intended = True
    if not intended:
        # Loose textual hint: "github" referenced anywhere notable.
        for key in ("integrations", "github"):
            if key in config:
                intended = True
                break
    if not intended:
        return CapabilityRow(intended=False, ready=False)
    source = _credential_source(env_values, "GITHUB_TOKEN", "GH_TOKEN")
    ok = source != "absent"
    return CapabilityRow(
        intended=True,
        ready=ok,
        reason="" if ok else "missing GITHUB_TOKEN or GH_TOKEN",
        source=source,
    )


def _check_memory(config: dict[str, Any], paths: Any) -> CapabilityRow:
    memory = config.get("memory")
    provider = ""
    if isinstance(memory, dict):
        provider = str(memory.get("provider") or "").strip().lower()
    intended = provider == "holographic"
    if not intended:
        return CapabilityRow(intended=False, ready=False)
    # The holographic provider writes under $HERMES_HOME. We treat the dir
    # being writable as readiness.
    hermes_home: Path = getattr(paths, "hermes_home", Path("/data/.hermes"))
    ok = _dir_writable(hermes_home)
    return CapabilityRow(intended=True, ready=ok, reason="" if ok else f"{hermes_home} not writable")


def _dir_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".readiness_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# Module-level paths — exposed so tests can monkeypatch them in-process.
# The Dockerfile writes the build SHA into _BUILD_REVISION_FILE at image
# build time (Track B). _WEBUI_VERSION_FILE is shipped inside the upstream
# hermes-webui image layer.
_BUILD_REVISION_FILE: Path = Path("/etc/hermes-station-build")
_WEBUI_VERSION_FILE: Path = Path("/opt/hermes-webui/VERSION")


def _read_image_revision() -> str | None:
    try:
        if _BUILD_REVISION_FILE.exists():
            txt = _BUILD_REVISION_FILE.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except OSError:
        pass
    return os.environ.get("HERMES_STATION_REVISION") or None


def _read_hermes_agent_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("hermes-agent")
        except PackageNotFoundError:
            return None
    except Exception:  # noqa: BLE001
        return None


def _read_hermes_webui_version() -> str | None:
    try:
        if _WEBUI_VERSION_FILE.exists():
            txt = _WEBUI_VERSION_FILE.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except OSError:
        pass
    return os.environ.get("HERMES_WEBUI_VERSION") or None


def _read_hermes_station_version() -> str:
    try:
        from hermes_station import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "0.0.0"


def _image_gen_intended(config: dict[str, Any]) -> bool:
    """True iff image_gen is intended — mirrors _check_image_gen's intent logic."""
    toolsets = config.get("toolsets")
    if isinstance(toolsets, list):
        if any(str(t).lower() == "image_gen" for t in toolsets):
            return True
    elif isinstance(toolsets, dict):
        block = toolsets.get("image_gen")
        if block is True:
            return True
        if isinstance(block, dict) and block.get("enabled", True):
            return True
    # fal: block present is also an intent signal (same as _check_image_gen).
    return isinstance(config.get("fal"), dict)


def _enabled_toolsets(config: dict[str, Any]) -> list[str]:
    toolsets = config.get("toolsets")
    out: list[str] = []
    if isinstance(toolsets, list):
        out = [str(t) for t in toolsets]
    elif isinstance(toolsets, dict):
        for name, val in toolsets.items():
            if val is True:
                out.append(str(name))
            elif isinstance(val, dict) and val.get("enabled", True):
                out.append(str(name))
    # Sync image_gen with the same intent signal used by _check_image_gen so
    # summary.toolsets and readiness.image_gen.intended always agree.
    if _image_gen_intended(config):
        if "image_gen" not in out:
            out.append("image_gen")
    else:
        out = [t for t in out if t != "image_gen"]
    return out


def _configured_platforms(config: dict[str, Any]) -> list[str]:
    out: list[str] = []
    messaging = config.get("messaging")
    if isinstance(messaging, dict):
        for slug, val in messaging.items():
            if val is True or (isinstance(val, dict) and val.get("enabled", True)):
                out.append(str(slug))
    channels = config.get("channels")
    if isinstance(channels, list):
        for c in channels:
            if str(c) not in out:
                out.append(str(c))
    elif isinstance(channels, dict):
        for slug, val in channels.items():
            if (val is True or (isinstance(val, dict) and val.get("enabled", True))) and str(slug) not in out:
                out.append(str(slug))
    return out


def validate_readiness(
    paths: Any,
    config: dict[str, Any] | None,
    env_values: dict[str, str] | None,
) -> Readiness:
    """Compute the full readiness report. Pure (modulo /etc + env reads)."""

    config = config or {}
    env_values = env_values or {}
    rows: dict[str, CapabilityRow] = {}

    # Channels (currently only discord per spec, but extend to all known slugs
    # so /health reflects each channel's status when intended).
    for entry in CHANNEL_CATALOG:
        slug = entry["slug"]
        intended = _channel_intended(config, slug)
        primary = entry["primary_key"]
        source = _credential_source(env_values, primary)
        ok = source != "absent"
        reason = "" if ok else f"missing {primary}"
        # Only record discord under its bare name (spec); other channels
        # use a "channel:<slug>" namespace to avoid noisy keys.
        key = "discord" if slug == "discord" else f"channel:{slug}"
        if intended or slug == "discord":
            rows[key] = CapabilityRow(
                intended=intended,
                ready=ok if intended else False,
                reason=reason if intended and not ok else "",
                source=source if intended else "",
            )

    # Primary model provider.
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    provider = str(model.get("provider") or "").strip().lower()
    if provider:
        rows[f"provider:{provider}"] = _check_provider(provider, env_values, intended=True)

    # Delegation provider(s).
    for dp in _delegation_providers(config):
        key = f"provider:{dp}"
        # Don't downgrade a ready row if already added.
        if key not in rows or not rows[key].ready:
            rows[key] = _check_provider(dp, env_values, intended=True)

    # Warn when a provider is configured but model.default is absent — auxiliary
    # tasks inside hermes-agent may silently fall back to the agent's own default
    # model, which differs per provider and may not be what the operator intends.
    if provider and not str(model.get("default") or "").strip():
        logger.warning(
            "model.default not set for provider %r; auxiliary tasks may silently use agent default model",
            provider,
            extra={"component": "readiness", "capability": "model_default", "provider": provider},
        )

    rows["web_search"] = _check_web_search(config, env_values)
    rows["image_gen"] = _check_image_gen(config, env_values)
    rows["github"] = _check_github(config, env_values)
    rows["memory:holographic"] = _check_memory(config, paths)

    # Versions.
    versions = {
        "hermes_station": _read_hermes_station_version(),
        "hermes_agent": _read_hermes_agent_version(),
        "hermes_webui": _read_hermes_webui_version(),
        "python": platform.python_version(),
        "image_revision": _read_image_revision(),
    }

    summary = {
        "image_revision": versions["image_revision"] or "dev",
        "hermes_agent": versions["hermes_agent"],
        "hermes_webui": versions["hermes_webui"],
        "python": versions["python"],
        "platforms": _configured_platforms(config),
        "toolsets": _enabled_toolsets(config),
    }

    readiness = Readiness(
        readiness=rows,
        versions=versions,
        boot_at=datetime.now(timezone.utc).isoformat(),
        summary=summary,
    )

    # Emit one structured log line per capability.
    for cap, row in rows.items():
        level = logging.INFO if row.ready or not row.intended else logging.WARNING
        logger.log(
            level,
            "readiness %s intended=%s ready=%s%s",
            cap,
            row.intended,
            row.ready,
            f" ({row.reason})" if row.reason else "",
            extra={
                "component": "readiness",
                "capability": cap,
                "intended": row.intended,
                "ready": row.ready,
            },
        )

    # Single boot_summary log line.
    logger.info(
        "boot_summary image=%s python=%s agent=%s webui=%s platforms=%s toolsets=%s",
        summary["image_revision"],
        summary["python"],
        summary["hermes_agent"],
        summary["hermes_webui"],
        summary["platforms"],
        summary["toolsets"],
        extra={"component": "readiness", "event": "boot_summary", **summary},
    )

    return readiness


__all__ = [
    "CapabilityRow",
    "Readiness",
    "validate_readiness",
]


# Silence "unused import" for sys — kept available for downstream callers.
_ = sys
