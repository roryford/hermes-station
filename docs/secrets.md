# Secrets management

The admin UI's **Secrets** page (`/admin/settings`) is the single place to
manage every API key the agent might use — provider keys (Anthropic, OpenAI,
…), image-generation backends (FAL), web-search backends (Brave, Tavily, …),
browser automation, and any custom secret you want to expose.

This page replaces the previous reality of "edit Railway env vars and hope it
takes effect" with explicit, observable state.

## The three states

Every secret resolves to one of three states:

| State        | Where it lives                                | Effect |
|--------------|-----------------------------------------------|--------|
| **Auto**     | nothing in `.env` or `admin.disabled_secrets` | Whatever Railway / host environment provides. Unset → unset. |
| **Override** | `KEY=value` in `$HERMES_HOME/.env`            | Takes precedence over Railway. Use when you need to substitute a different value than what's in Railway. |
| **Disabled** | `KEY` in `admin.disabled_secrets`             | Actively suppressed: popped from `os.environ` after `.env` merge, so the agent sees nothing even if Railway provides a value. |

The **Source** badge on each row tells you which one is currently in effect:

- `env` — coming from Railway / host
- `file` — overridden by `.env`
- `disabled` — actively suppressed
- `unset` — nowhere

## Shadowing

The `.env` file wins over Railway. If you save an override on the Secrets page
and Railway *also* sets the same key, the override takes effect and you'll see
a **⚠ Railway also sets …** warning on the row. This is intentional but
surprising — left undetected, it's the most common cause of "I added the env
var to Railway but the agent still uses the old value." Click **Use Railway**
to drop the override.

## Common patterns

### "I added `FAL_KEY` to Railway but the agent doesn't see it"

Most likely cause: a stale value in `.env` is shadowing it. Open the Secrets
page, find FAL_KEY, click **Use Railway** to drop the override.

### "I want to temporarily disable image generation without touching Railway"

Find FAL_KEY on the Secrets page, click **Disable**. The agent will not see
the key on the next restart and will report `image_gen` as not ready in
`/health`. Click **Re-enable** to restore.

### "I want to rotate a key"

Click **Save override** with the new value. The old value is overwritten in
`.env` atomically. Restart the agent (the page does this automatically).

### "I have a custom service with its own API key"

Use **Add custom secret** at the bottom of the page. The key gets tracked in
`admin.custom_secret_keys` so it renders on the page on future visits.

## In-process tools vs sandboxed tools

The agent runs most tools **in-process**: image generation, web search,
model providers, etc. They read secrets directly from `os.environ` and need
no special configuration — Railway env vars and `.env` overrides Just Work.

A few tools run in **isolated subprocesses** for safety: the terminal tool,
code execution, and MCP servers (filesystem, GitHub, …). These do NOT inherit
the parent's environment by default. To expose a secret to them, list its key
in `terminal.env_passthrough` in `config.yaml`. The Secrets page exposes this
as the **"Also expose to sandboxed tools"** checkbox when adding a custom
secret — leave it unchecked unless you know a sandboxed tool needs the key.

`GITHUB_TOKEN`, `GH_TOKEN`, and other well-known keys are auto-added to
`terminal.env_passthrough` at boot — see `hermes_station/app.py`.

## Storage on disk

- Overrides: `$HERMES_HOME/.env` (mode 0600, atomic writes)
- Disabled keys: `admin.disabled_secrets: [LIST]` in `config.yaml`
- Custom tracked keys: `admin.custom_secret_keys: [LIST]` in `config.yaml`

See [`CONTRACT.md`](./CONTRACT.md) §4 for the on-disk format guarantees.
