"""Secrets page domain helpers.

Provides the data model and persistence helpers for the admin "Secrets" page.
Three states per secret: ``auto`` (use whatever the environment provides),
``override`` (a value in ``.env`` takes precedence), and ``disabled`` (the key
is actively popped from ``os.environ`` after .env seeding, so the agent sees
nothing even when Railway provides a value).

The catalog is sourced from the same lists ``readiness.py`` uses, so the
Secrets page never drifts from "what readiness actually checks for". Operators
can add arbitrary additional keys via the UI; these are tracked in
``config.yaml`` under ``admin.custom_secret_keys`` so we know which non-catalog
keys to render. The "disabled" set lives at ``admin.disabled_secrets``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from hermes_station.config import (
    load_env_file,
    load_yaml_config,
    write_env_file,
    write_yaml_config,
)
from hermes_station.secrets import mask


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
#
# Each entry: key (env var), label (human), group (display section), url
# (signup/docs link), hint (one-liner under the field), and ``in_process``
# (whether the consuming tool runs inside the agent process — when True the
# "expose to sandboxed tools" hint is suppressed since it's irrelevant).
#
# Groups are display-only; they control section ordering on the page.

CATALOG_GROUPS: tuple[str, ...] = ("image_gen", "web_search", "browser", "other")


KNOWN_SECRETS: tuple[dict[str, Any], ...] = (
    {
        "key": "FAL_KEY",
        "label": "FAL.ai",
        "group": "image_gen",
        "url": "https://fal.ai/dashboard/keys",
        "hint": "Image generation backend (flux, gpt-image, nano-banana, etc.).",
        "in_process": True,
    },
    {
        "key": "BRAVE_API_KEY",
        "label": "Brave Search",
        "group": "web_search",
        "url": "https://brave.com/search/api/",
        "hint": "Free tier available.",
        "in_process": True,
    },
    {
        "key": "TAVILY_API_KEY",
        "label": "Tavily",
        "group": "web_search",
        "url": "https://app.tavily.com/home",
        "hint": "1000 free searches per month.",
        "in_process": True,
    },
    {
        "key": "SERPAPI_API_KEY",
        "label": "SerpAPI",
        "group": "web_search",
        "url": "https://serpapi.com/",
        "hint": "Google / Bing / DuckDuckGo result scraping.",
        "in_process": True,
    },
    {
        "key": "GOOGLE_CSE_API_KEY",
        "label": "Google Custom Search",
        "group": "web_search",
        "url": "https://developers.google.com/custom-search/v1/overview",
        "hint": "Requires a Programmable Search Engine ID alongside the key.",
        "in_process": True,
    },
    {
        "key": "FIRECRAWL_API_KEY",
        "label": "Firecrawl",
        "group": "web_search",
        "url": "https://firecrawl.dev/",
        "hint": "Web scraping with rendered-page extraction.",
        "in_process": True,
    },
    {
        "key": "BROWSERBASE_API_KEY",
        "label": "Browserbase",
        "group": "browser",
        "url": "https://browserbase.com/",
        "hint": "Managed headless browser sessions. Pair with BROWSERBASE_PROJECT_ID.",
        "in_process": True,
    },
    {
        "key": "BROWSERBASE_PROJECT_ID",
        "label": "Browserbase Project ID",
        "group": "browser",
        "url": "https://browserbase.com/",
        "hint": "Project identifier paired with BROWSERBASE_API_KEY.",
        "in_process": True,
    },
    {
        "key": "BROWSER_USE_API_KEY",
        "label": "Browser Use",
        "group": "browser",
        "url": "https://cloud.browser-use.com/",
        "hint": "Browser Use cloud. Free tier available. Auto-detected by hermes-agent when set.",
        "in_process": True,
    },
    {
        "key": "CAMOFOX_URL",
        "label": "Camofox",
        "group": "browser",
        "url": "https://github.com/jo-inc/camofox-browser",
        "hint": (
            "URL of your self-hosted Camofox service "
            "(e.g. http://camofox.railway.internal:9377). "
            "Takes priority over all cloud providers when set."
        ),
        "in_process": True,
    },
    {
        "key": "STEEL_API_KEY",
        "label": "Steel",
        "group": "browser",
        "url": "https://app.steel.dev/",
        "hint": "Steel cloud. 100 hrs/month free. Requires hermes-agent ≥ next release.",
        "in_process": True,
    },
)


_KEY_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# Maximum env-var name length we accept from the UI. Bounded to keep .env
# reasonable and avoid accidental paste of an entire value into the key field.
_MAX_KEY_LEN = 128


def is_valid_key_name(name: str) -> bool:
    """True iff *name* is a syntactically valid env var name (POSIX-ish)."""
    if not name or len(name) > _MAX_KEY_LEN:
        return False
    return bool(_KEY_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# Config-yaml accessors
# ---------------------------------------------------------------------------


def _admin_block(config: dict[str, Any]) -> dict[str, Any]:
    admin = config.get("admin")
    return admin if isinstance(admin, dict) else {}


def get_custom_keys(config: dict[str, Any]) -> list[str]:
    """Return the list of user-added custom secret keys, in insertion order."""
    raw = _admin_block(config).get("custom_secret_keys")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item).strip()
        if key and key not in seen and is_valid_key_name(key):
            out.append(key)
            seen.add(key)
    return out


def get_disabled_keys(config: dict[str, Any]) -> set[str]:
    """Return the set of keys to actively suppress from the agent's env."""
    raw = _admin_block(config).get("disabled_secrets")
    if not isinstance(raw, list):
        return set()
    return {str(k).strip() for k in raw if str(k).strip()}


def _set_admin_field(config_path: Path, field: str, value: Any) -> None:
    config = load_yaml_config(config_path)
    admin = config.get("admin")
    if not isinstance(admin, dict):
        admin = {}
    admin[field] = value
    config["admin"] = admin
    write_yaml_config(config_path, config)


# ---------------------------------------------------------------------------
# Status — what the UI renders
# ---------------------------------------------------------------------------


def _catalog_entry(key: str) -> dict[str, Any] | None:
    for entry in KNOWN_SECRETS:
        if entry["key"] == key:
            return entry
    return None


def _resolve_state(
    key: str,
    env_values: dict[str, str],
    environ: dict[str, str],
    disabled: set[str],
) -> dict[str, Any]:
    """Compute the source/state/shadow flags for a single key.

    Returns a dict with: state (auto|override|disabled), source
    (env|file|unset|disabled), masked_value, shadowed (.env and Railway both
    set with different values), and railway_value (masked Railway value, for
    the "use Railway" affordance to show what would take effect).
    """
    file_val = env_values.get(key, "")
    # We snapshot os.environ once and pass it in. Note: .env seeding overwrites
    # os.environ at boot, so a value present in both will look identical in
    # environ. To detect a true shadow we compare against file_val directly.
    env_val = environ.get(key, "")

    if key in disabled:
        return {
            "state": "disabled",
            "source": "disabled",
            "masked_value": "",
            "shadowed": False,
            "railway_value": "",
        }

    if file_val:
        # Override mode. The "shadow" warning is meaningful only when Railway
        # has a *different* value than the override — same value is just a
        # redundant override, not a surprising shadow.
        shadowed = bool(env_val) and env_val != file_val
        return {
            "state": "override",
            "source": "file",
            "masked_value": mask(file_val),
            "shadowed": shadowed,
            "railway_value": mask(env_val) if env_val else "",
        }

    if env_val:
        return {
            "state": "auto",
            "source": "env",
            "masked_value": mask(env_val),
            "shadowed": False,
            "railway_value": mask(env_val),
        }

    return {
        "state": "auto",
        "source": "unset",
        "masked_value": "",
        "shadowed": False,
        "railway_value": "",
    }


def _ordered_keys_for_render(
    catalog: tuple[dict[str, Any], ...],
    custom_keys: list[str],
) -> list[tuple[str, dict[str, Any] | None]]:
    """Catalog rows first (grouped), then custom rows, then disabled-only keys.

    Returned tuple: (key, catalog_entry_or_None). custom keys carry None.
    """
    out: list[tuple[str, dict[str, Any] | None]] = []
    seen: set[str] = set()
    for group in CATALOG_GROUPS:
        for entry in catalog:
            if entry["group"] != group or entry["key"] in seen:
                continue
            out.append((entry["key"], entry))
            seen.add(entry["key"])
    for key in custom_keys:
        if key in seen:
            continue
        out.append((key, None))
        seen.add(key)
    return out


def secret_status(
    config: dict[str, Any],
    env_values: dict[str, str],
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the full Secrets-page context.

    Returns a dict with two lists of rows (catalog + custom) and a flat
    ``groups`` list grouping catalog rows by display section. Each row has the
    catalog metadata (or stubs for custom keys), the resolved state, masked
    values, and shadow flags.
    """
    environ = environ if environ is not None else dict(os.environ)
    custom_keys = get_custom_keys(config)
    disabled = get_disabled_keys(config)

    # Surface a key that's only known via the disabled list — gives the user
    # somewhere to click "re-enable" even if the key was disabled and then
    # removed from custom_secret_keys.
    extras = [k for k in disabled if k not in {e["key"] for e in KNOWN_SECRETS} and k not in custom_keys]

    rows: list[dict[str, Any]] = []
    for key, entry in _ordered_keys_for_render(KNOWN_SECRETS, custom_keys + extras):
        state = _resolve_state(key, env_values, environ, disabled)
        if entry is not None:
            row = {
                "key": key,
                "label": entry["label"],
                "group": entry["group"],
                "url": entry.get("url", ""),
                "hint": entry.get("hint", ""),
                "in_process": entry.get("in_process", False),
                "is_custom": False,
            }
        else:
            row = {
                "key": key,
                "label": key,
                "group": "custom",
                "url": "",
                "hint": "",
                "in_process": False,
                "is_custom": True,
            }
        row.update(state)
        rows.append(row)

    groups: list[dict[str, Any]] = []
    for group in (*CATALOG_GROUPS, "custom"):
        group_rows = [r for r in rows if r["group"] == group]
        if not group_rows:
            continue
        groups.append({"slug": group, "label": _GROUP_LABELS[group], "rows": group_rows})

    return {"groups": groups, "rows": rows}


_GROUP_LABELS: dict[str, str] = {
    "image_gen": "Image generation",
    "web_search": "Web search",
    "browser": "Browser automation",
    "other": "Other",
    "custom": "Custom",
}


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
#
# All mutations return None on success and raise ValueError on bad input.
# Invariants:
#   - .env writes always go through write_env_file (mode 0600, atomic).
#   - config.yaml writes always go through write_yaml_config.
#   - Setting an override clears the disabled flag (the two states are
#     mutually exclusive from the user's point of view).
#   - Disabling a key does NOT remove an existing .env override — the
#     disabled list pops from os.environ after seeding, so an override row
#     stays in .env (visible if you re-enable) but is shadowed by the pop.


def _ensure_known_or_custom(config_path: Path, key: str) -> None:
    """Track *key* as a custom secret if it isn't in the static catalog."""
    if any(entry["key"] == key for entry in KNOWN_SECRETS):
        return
    config = load_yaml_config(config_path)
    custom = get_custom_keys(config)
    if key in custom:
        return
    custom.append(key)
    _set_admin_field(config_path, "custom_secret_keys", custom)


def save_override(env_path: Path, config_path: Path, key: str, value: str) -> None:
    """Set an override value in .env. Auto-registers custom keys."""
    if not is_valid_key_name(key):
        raise ValueError(f"invalid key name: {key!r}")
    clean = value.strip()
    if not clean:
        raise ValueError("value must not be empty (use 'Use Railway' to clear an override)")
    if "\n" in clean or "\r" in clean:
        raise ValueError(f"{key} must not contain newline characters")
    values = load_env_file(env_path)
    values[key] = clean
    write_env_file(env_path, values)
    # Setting an override implies "use this" — clear any prior disabled flag.
    _set_disabled(config_path, key, disabled=False)
    _ensure_known_or_custom(config_path, key)


def clear_override(env_path: Path, key: str) -> None:
    """Remove *key* from .env. The Railway value (if any) takes effect."""
    if not is_valid_key_name(key):
        raise ValueError(f"invalid key name: {key!r}")
    values = load_env_file(env_path)
    if values.pop(key, None) is not None:
        write_env_file(env_path, values)


def disable(config_path: Path, key: str) -> None:
    """Add *key* to the disabled set. Active across reboots until re-enabled."""
    if not is_valid_key_name(key):
        raise ValueError(f"invalid key name: {key!r}")
    _set_disabled(config_path, key, disabled=True)
    _ensure_known_or_custom(config_path, key)


def enable(config_path: Path, key: str) -> None:
    """Remove *key* from the disabled set."""
    if not is_valid_key_name(key):
        raise ValueError(f"invalid key name: {key!r}")
    _set_disabled(config_path, key, disabled=False)


def _set_disabled(config_path: Path, key: str, *, disabled: bool) -> None:
    config = load_yaml_config(config_path)
    current = get_disabled_keys(config)
    if disabled:
        if key in current:
            return
        current.add(key)
    else:
        if key not in current:
            return
        current.discard(key)
    _set_admin_field(config_path, "disabled_secrets", sorted(current))


def add_custom_key(config_path: Path, key: str) -> None:
    """Register *key* as a tracked custom secret without setting a value."""
    if not is_valid_key_name(key):
        raise ValueError(f"invalid key name: {key!r}")
    _ensure_known_or_custom(config_path, key)


def forget_custom_key(env_path: Path, config_path: Path, key: str) -> None:
    """Remove a custom secret entirely: untrack it and clear .env override.

    Does NOT touch the disabled list — if the user disabled it and then
    forgets it, re-adding the key should restore the disabled state. (The
    Secrets page surfaces orphaned disabled keys via the ``extras`` branch
    in ``secret_status``, so this is recoverable.)
    """
    if not is_valid_key_name(key):
        raise ValueError(f"invalid key name: {key!r}")
    config = load_yaml_config(config_path)
    custom = [k for k in get_custom_keys(config) if k != key]
    _set_admin_field(config_path, "custom_secret_keys", custom)
    clear_override(env_path, key)


__all__ = [
    "CATALOG_GROUPS",
    "KNOWN_SECRETS",
    "add_custom_key",
    "clear_override",
    "disable",
    "enable",
    "forget_custom_key",
    "get_custom_keys",
    "get_disabled_keys",
    "is_valid_key_name",
    "save_override",
    "secret_status",
]
