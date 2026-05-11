"""Channel catalog + .env channel save helper.

Ported from hermes-all-in-one's `control_plane/config.py`. Slugs are part of
the data contract (CONTRACT.md §7) — they must not be renamed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_station.config import load_env_file, write_env_file
from hermes_station.secrets import mask


CHANNEL_CATALOG: list[dict[str, str]] = [
    {
        "slug": "telegram",
        "label": "Telegram",
        "primary_key": "TELEGRAM_BOT_TOKEN",
        "secondary_key": "TELEGRAM_ALLOWED_USERS",
        "hint": "Bot token, plus optional allowlist or home channel settings in .env.",
    },
    {
        "slug": "discord",
        "label": "Discord",
        "primary_key": "DISCORD_BOT_TOKEN",
        "secondary_key": "DISCORD_ALLOWED_USERS",
        "hint": "Bot token, plus optional allowlist.",
    },
    {
        "slug": "slack",
        "label": "Slack",
        "primary_key": "SLACK_BOT_TOKEN",
        "secondary_key": "SLACK_APP_TOKEN",
        "hint": "Bot token and optional app token.",
    },
    {
        "slug": "whatsapp",
        "label": "WhatsApp",
        "primary_key": "WHATSAPP_ENABLED",
        "secondary_key": "",
        "hint": "Set to 1/true when WhatsApp is configured externally.",
    },
    {
        "slug": "email",
        "label": "Email",
        "primary_key": "EMAIL_ADDRESS",
        "secondary_key": "EMAIL_PASSWORD",
        "hint": "Mailbox address and password/app password.",
    },
]


CHANNEL_ENV_KEYS: tuple[str, ...] = tuple(
    key
    for entry in CHANNEL_CATALOG
    for key in (entry["primary_key"], entry["secondary_key"])
    if key
)


_WHATSAPP_ON = {"1", "true", "yes", "on"}


def channel_status(env_values: dict[str, str]) -> list[dict[str, Any]]:
    """Per-channel state for the admin UI. Secrets are masked."""
    out: list[dict[str, Any]] = []
    for entry in CHANNEL_CATALOG:
        primary = env_values.get(entry["primary_key"], "").strip()
        secondary_key = entry["secondary_key"]
        secondary = env_values.get(secondary_key, "").strip() if secondary_key else ""
        # WhatsApp is a flag, not a token — enabled means the flag is truthy.
        if entry["slug"] == "whatsapp":
            enabled = primary.lower() in _WHATSAPP_ON
        else:
            enabled = bool(primary)
        out.append(
            {
                "slug": entry["slug"],
                "label": entry["label"],
                "enabled": enabled,
                "primary_key": entry["primary_key"],
                "secondary_key": secondary_key,
                "primary_value": mask(primary),
                "secondary_value": mask(secondary),
                "hint": entry["hint"],
            }
        )
    return out


def save_channel_values(env_path: Path, updates: dict[str, str | None]) -> dict[str, str]:
    """Apply a partial dict of channel env updates to .env.

    `None` (or empty string) deletes a key. Only keys in `CHANNEL_ENV_KEYS` are
    accepted; anything else is silently ignored to keep the surface tight.
    Returns the resulting full env dict.
    """
    allowed = set(CHANNEL_ENV_KEYS)
    values = load_env_file(env_path)
    for key, value in updates.items():
        if key not in allowed:
            continue
        if value is None:
            values.pop(key, None)
            continue
        clean = str(value).strip()
        if not clean:
            values.pop(key, None)
            continue
        if "\n" in clean or "\r" in clean:
            raise ValueError(f"{key} must not contain newline characters")
        values[key] = clean
    write_env_file(env_path, values)
    return values
