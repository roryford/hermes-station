# Secrets management

Secrets (API keys, channel tokens, etc.) can be supplied in two ways:

1. **Railway / Docker env vars** — set in the Railway dashboard Variables tab
   or via `-e` on `docker run` / `container run`. Picked up on every container
   start.
2. **`/data/.hermes/.env`** — a dotenv file written by hermes-webui and read at
   boot. Values here take precedence over process env vars. Use the WebUI
   settings panel to manage this file at runtime.

## The three states

Every secret resolves to one of three states:

| State        | Where it lives                                | Effect |
|--------------|-----------------------------------------------|--------|
| **Auto**     | nothing in `.env` or `admin.disabled_secrets` | Whatever Railway / host environment provides. Unset → unset. |
| **Override** | `KEY=value` in `$HERMES_HOME/.env`            | Takes precedence over Railway. Use when you need to substitute a different value than what's in Railway. |
| **Disabled** | `KEY` in `admin.disabled_secrets`             | Actively suppressed: popped from the environment after `.env` merge, so the agent sees nothing even if Railway provides a value. |

## Common patterns

### "I added `FAL_KEY` to Railway but the agent doesn't see it"

Most likely cause: a stale value in `.env` is shadowing it. Open the WebUI
settings, find FAL_KEY, and drop the `.env` override (choose "Use Railway").

### "I want to temporarily disable image generation without touching Railway"

Disable FAL_KEY via the WebUI settings. The agent will not see the key on the
next restart and will report `image_gen` as not ready in `/health`. Re-enable
to restore.

### "I want to rotate a key"

1. Update the value in the Railway dashboard Variables tab.
2. Restart the service.

Or override it via the WebUI settings — the new value is written atomically to
`.env` and the gateway restarts automatically.

## In-process tools vs sandboxed tools

The agent runs most tools **in-process**: image generation, web search,
model providers, etc. They read secrets directly from the environment and need
no special configuration.

A few tools run in **isolated subprocesses** for safety: the terminal tool,
code execution, and MCP servers (filesystem, GitHub, …). These do NOT inherit
the parent's environment by default. To expose a secret to them, list its key
in `terminal.env_passthrough` in `config.yaml`.

`GITHUB_TOKEN` and `GH_TOKEN` are auto-added to `terminal.env_passthrough` at
boot.

## Storage on disk

- Overrides: `$HERMES_HOME/.env` (mode 0600, atomic writes)
- Disabled keys: `admin.disabled_secrets: [LIST]` in `config.yaml`
- Custom tracked keys: `admin.custom_secret_keys: [LIST]` in `config.yaml`
