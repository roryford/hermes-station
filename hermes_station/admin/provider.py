"""Provider catalog + provider setup helper.

Ported from hermes-all-in-one's `control_plane/config.py` and extended for the
providers supported by the pinned `hermes-agent`. The catalog IDs
(`openrouter`, `anthropic`, `openai`, `copilot`, `custom`) are part of the
data contract (CONTRACT.md §6) — they must not be renamed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hermes_station.config import (
    extract_model_config,
    load_env_file,
    load_yaml_config,
    write_env_file,
    write_yaml_config,
)


PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "openrouter": {
        "label": "OpenRouter",
        "env_var": "OPENROUTER_API_KEY",
        "default_model": "anthropic/claude-sonnet-4.6",
        "requires_base_url": False,
    },
    "anthropic": {
        "label": "Anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4.6",
        "requires_base_url": False,
    },
    "openai": {
        "label": "OpenAI",
        "env_var": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com/v1",
        "requires_base_url": False,
        "credential_label": "API key",
        "credential_placeholder": "Leave blank to keep existing key",
        "credential_hint": "Leave blank to keep the stored key. Required only for first setup or key rotation.",
    },
    "copilot": {
        "label": "GitHub Copilot",
        "env_var": "COPILOT_GITHUB_TOKEN",
        "accepted_env_vars": ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        "default_model": "gpt-4.1",
        "requires_base_url": False,
        "credential_label": "GitHub token",
        "credential_placeholder": "Paste a Copilot-capable GitHub token",
        "credential_hint": (
            "Saves as COPILOT_GITHUB_TOKEN. Supported tokens include gho_ OAuth tokens, "
            "github_pat_ fine-grained PATs with Copilot Requests permission, and ghu_ app tokens. "
            "Classic ghp_* PATs are not supported."
        ),
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "env_var": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "requires_base_url": True,
        "credential_label": "API key",
        "credential_placeholder": "Leave blank to keep existing key",
        "credential_hint": "Leave blank to keep the stored key. Required only for first setup or key rotation.",
    },
}


UNSUPPORTED_PROVIDER_NOTE = (
    "OAuth and advanced provider flows such as OpenAI Codex, ChatGPT-style subscription login, "
    "and Nous Portal are still advanced/manual in hosted Railway deployments. "
    "Use terminal-first Hermes auth/model flows for those providers instead of relying on in-browser OAuth."
)


def provider_env_var_names(provider: str) -> tuple[str, ...]:
    """Return the env vars that count as credentials for a provider."""
    meta = PROVIDER_CATALOG.get((provider or "").strip().lower(), {})
    accepted = meta.get("accepted_env_vars")
    if isinstance(accepted, (list, tuple)):
        names = tuple(str(name).strip() for name in accepted if str(name).strip())
        if names:
            return names
    env_var = str(meta.get("env_var") or "").strip()
    return (env_var,) if env_var else ()


def provider_has_credentials(provider: str, env_values: dict[str, str]) -> bool:
    """Check .env + process env for any accepted provider credential.

    .env values take precedence over process env (CONTRACT.md §2.1).
    """
    return any(
        env_values.get(env_var, "").strip() or os.environ.get(env_var, "").strip()
        for env_var in provider_env_var_names(provider)
    )


def apply_provider_setup(
    *,
    config_path: Path,
    env_path: Path,
    provider: str,
    model: str,
    api_key: str,
    base_url: str = "",
) -> dict[str, str]:
    """Persist provider/model to config.yaml and the API key to .env.

    Both files are written 0600 via the underlying write helpers. Returns the
    minimal trio the admin UI needs to confirm which env var was set.
    """
    provider = (provider or "").strip().lower()
    if provider not in PROVIDER_CATALOG:
        raise ValueError(f"Unsupported provider: {provider}")
    meta = PROVIDER_CATALOG[provider]
    model = (model or meta["default_model"]).strip()
    base_url = (base_url or meta.get("default_base_url") or "").strip().rstrip("/")
    if meta.get("requires_base_url") and not base_url:
        raise ValueError("base_url is required for custom providers")
    if not model:
        raise ValueError("model is required")
    if not api_key:
        env_values = load_env_file(env_path)
        api_key = (
            env_values.get(meta["env_var"], "").strip()
            or os.environ.get(meta["env_var"], "").strip()
        )
        if not api_key:
            raise ValueError(
                f"No existing {meta['env_var']} found — please paste your API key."
            )

    config = load_yaml_config(config_path)
    raw_model = config.get("model")
    # Preserve unknown keys round-trip (CONTRACT.md §4.2).
    model_cfg: dict[str, Any] = dict(raw_model) if isinstance(raw_model, dict) else {}
    model_cfg["provider"] = provider
    model_cfg["default"] = model
    if base_url:
        model_cfg["base_url"] = base_url
    else:
        model_cfg.pop("base_url", None)
    config["model"] = model_cfg
    write_yaml_config(config_path, config)

    # write_env_file expects the full env dict, not a delta — load + merge.
    env_values = load_env_file(env_path)
    env_values[meta["env_var"]] = api_key
    write_env_file(env_path, env_values)

    return {"provider": provider, "model": model, "env_var": meta["env_var"]}


def provider_status(config: dict[str, Any], env_values: dict[str, str]) -> dict[str, Any]:
    """Provider block for /admin/api/status: provider/default/base_url + readiness."""
    model = extract_model_config(config)
    provider = model.provider.lower()
    ready = bool(provider and provider_has_credentials(provider, env_values))
    return {
        "provider": model.provider,
        "default": model.default,
        "base_url": model.base_url,
        "ready": ready,
    }
