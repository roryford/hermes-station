# Configuration

hermes-station ships with **warn-and-continue defaults**. Missing secrets surface as `ready: false` rows in `/health` rather than crashing the container, so a stock image always boots and the FIRST RUN wizard in the WebUI walks you through the rest.

## Environment variables

hermes-station reads the following from the process environment. Anything not set falls back to a built-in default (or marks the corresponding capability as not-ready).

### Auth & sessions

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_WEBUI_PASSWORD` | _unset_ | Password for the WebUI at `/`. Set this before any non-local deploy. |

### Process & paths

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_GATEWAY_ENABLED` | _unset_ | Set to `1` to start the messaging gateway (Discord, Telegram, Slack) and enable scheduled/cron jobs. |
| `HINDSIGHT_SIDECAR` | _unset_ | Set to `1` to start the Hindsight memory sidecar. |
| `HERMES_HOME` | `/data/.hermes` | Hermes runtime home. |
| `HERMES_CONFIG_PATH` | `/data/.hermes/config.yaml` | Path to the active config file. |
| `HERMES_WEBUI_STATE_DIR` | `/data/webui` | WebUI per-user state. |
| `HERMES_WORKSPACE_DIR` | `/data/workspace` | Agent workspace root. |
| `PORT` | `8787` | Bind port. |

### Secrets that unlock capabilities

All of these follow the warn-and-continue rule: if the capability is referenced in `config.yaml` but the secret is missing, `/health` reports it as not-ready with a human-readable reason. The container keeps running.

| Variable | Capability |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Discord channel |
| `OPENROUTER_API_KEY` | OpenRouter LLM provider |
| `COPILOT_GITHUB_TOKEN` | GitHub Copilot LLM provider (also accepts `GH_TOKEN` / `GITHUB_TOKEN` as fallbacks for the Copilot pool) |
| `OPENAI_API_KEY` | OpenAI provider |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) | Google Gemini provider |
| `BRAVE_API_KEY` | Brave web search backend |
| `GITHUB_TOKEN` / `GH_TOKEN` | `gh` CLI inside terminals, GitHub MCP server |
| `FAL_KEY` | Image generation (fal.ai backend) |
| `EMAIL_ADDRESS` | Email channel (himalaya IMAP/SMTP) |
| `EMAIL_PASSWORD` | Email channel — app password, not account password |
| `EMAIL_DISPLAY_NAME` | Optional display name for outgoing email (`"Name" <address>`) |

## First-boot config seeding

On the first start against a fresh `/data`, hermes-webui writes a minimal `config.yaml` containing:

- **Holographic memory** provider on by default.
- A curated set of **MCP servers** added but `enabled: false` — they appear in the WebUI settings ready to toggle on once you've supplied any keys they need.
- A **neutral personality default** (no opinionated system-prompt overlay).
- `display.show_cost: true` so token costs surface in the WebUI.

Seeding is **no-clobber**: any value already present in `config.yaml` wins. Re-running the container against a populated `/data` will not overwrite your settings.

A minimal annotated starter that boots cleanly with zero secrets (degraded but running) is at [`config.example.yaml`](config.example.yaml).

### Provider auto-seed

If you set one of the provider env vars below at first boot, hermes-station writes a matching `model:` block to `config.yaml` automatically — no manual settings step required.

| Env var               | Seeded `model.provider` | Default `model.name`            |
| --------------------- | ----------------------- | ------------------------------- |
| `OPENROUTER_API_KEY`  | `openrouter`            | `anthropic/claude-sonnet-4.5`   |
| `ANTHROPIC_API_KEY`   | `anthropic`             | `claude-sonnet-4-5`             |
| `OPENAI_API_KEY`      | `openai`                | `gpt-4.1`                       |

Rules:

- **Precedence is table order.** If multiple keys are set, the first non-empty one wins (OpenRouter first because it's the template's headline path). Empty / whitespace-only values are treated as unset.
- **No-clobber is absolute.** If `config.yaml` has *any* `model:` block — even a partial one like `model: {name: foo}` with no `provider` — the seeder skips. Operators who edited the file get to keep their state.

### Email (himalaya) config auto-seed

When `EMAIL_ADDRESS` and `EMAIL_PASSWORD` are both set, hermes-station writes `~/.config/himalaya/config.toml` on every container start. IMAP/SMTP settings are inferred from the email domain:

| Domain | IMAP host | SMTP host |
|---|---|---|
| `gmail.com`, `googlemail.com` | `imap.gmail.com` | `smtp.gmail.com` |
| `icloud.com`, `me.com`, `mac.com` | `imap.mail.me.com` | `smtp.mail.me.com` |
| anything else | `imap.<domain>` | `smtp.<domain>` |

All entries use port 993 TLS for IMAP and port 587 STARTTLS for SMTP.

### Runtime version patches

The container ships a pinned combination of hermes-agent and hermes-webui, but you can override either at boot — useful for testing an upstream release before rebaking the image.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_PATCH_AGENT_VERSION` | _unset_ | When set to a version string (e.g. `0.14.1`), the entrypoint upgrades `hermes-agent` to that version before the webui boots. |
| `HERMES_PATCH_WEBUI_VERSION` | _unset_ | Same as above but for `hermes-webui`. |

Both vars are honored only at container start (the entrypoint runs the upgrade and then hands off to supervisord). Unset to revert to the image-baked versions on the next restart.

## Capabilities reference

For the full list of what hermes-station supports (LLM providers, channels, voice, memory, web search, browser automation, image generation, observability, MCP tools) plus how to enable each, see [`features.md`](./features.md).

## Build metadata

### `IMAGE_REVISION`

The Dockerfile accepts `--build-arg IMAGE_REVISION=<git-sha>`, defaulting to `${RAILWAY_GIT_COMMIT_SHA:-dev}`. The value is written to `/etc/hermes-station-build` inside the image and surfaced on `/health` as `versions.image_revision`.

## Health surface

Three endpoints:

- `/health/live` — liveness probe, always cheap, 200 while the process is up.
- `/health/ready` — readiness probe, returns `503` when the composite status is `degraded` or `down`.
- `/health` — full JSON, **always 200**, with a top-level `status` of `ok` / `degraded` / `down` and a `readiness` map of per-capability `{ready, reason}` rows.

### The warn-and-continue model

For every capability listed in `config.yaml`, hermes-station performs a startup probe (checks that the required secret is present, the dependency reachable, etc.). The outcomes:

- **All probes pass** → `status: "ok"`, all rows `ready: true`.
- **One or more probes fail because of missing config/secrets** → `status: "degraded"`, the failing rows carry `ready: false` and a human-readable `reason`. **The container does not exit.**
- **A core subsystem is dead** → `status: "down"` and `/health/ready` returns `503`.

## Logs

Stdout is JSON, one object per line. Each record carries at minimum: `ts`, `level`, `component`, `event`, `message`, plus contextual extras.

Useful `jq` filters:

```bash
# Gateway / channel runtime (Discord, Telegram, Slack)
container logs hermes-station | jq 'select(.component=="gateway")'

# Everything at warning or above
container logs hermes-station | jq 'select(.level=="warning" or .level=="error")'
```
