# Configuration

hermes-station ships with **warn-and-continue defaults**. Missing secrets surface as `ready: false` rows in `/health` rather than crashing the container, so a stock image always boots and the admin UI can walk you through the rest.

## Environment variables

hermes-station reads the following from the process environment. Anything not set falls back to a built-in default (or marks the corresponding capability as not-ready).

### Auth & sessions

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_ADMIN_PASSWORD` | _unset_ | Password for `/admin`. Falls back to `HERMES_WEBUI_PASSWORD` if that is set; if both are unset, `/admin` is open. Set this before exposing a public deployment. |
| `HERMES_WEBUI_PASSWORD` | _unset_ | Password for the WebUI at `/`. Same hardening note as above. |
| `HERMES_ADMIN_SESSION_TTL` | `86400` | Admin session lifetime in seconds. |

> **Hardening note:** for any deployment that isn't a localhost dev loop, set both passwords. `/health` will still report `ready: true` for the auth surface either way; the warning is operational, not capability-level.

### Process & paths

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_GATEWAY_AUTOSTART` | `auto` | `auto` / `on` / `off`. Whether the gateway boots with the container. |
| `HERMES_HOME` | `/data/.hermes` | Hermes runtime home. |
| `HERMES_CONFIG_PATH` | `/data/.hermes/config.yaml` | Path to the active config file. |
| `HERMES_WEBUI_STATE_DIR` | `/data/webui` | WebUI per-user state. |
| `HERMES_WORKSPACE_DIR` | `/data/workspace` | Agent workspace root. |
| `HERMES_WEBUI_SRC` | _baked into image_ | Override path to a WebUI checkout (dev only). |
| `CONTROL_PLANE_HOST` | `0.0.0.0` | Bind host for the control plane. |
| `PORT` | `8787` | Bind port. |
| `TRUSTED_PROXY_IPS` | _unset_ | Comma-separated list of proxy IPs whose `X-Forwarded-*` headers are honored. |

### Secrets that unlock capabilities

All of these follow the warn-and-continue rule: if the capability is referenced in `config.yaml` but the secret is missing, `/health.readiness.<capability>` reports `ready: false` with a `reason`, and a structured warning is logged at boot. The container keeps running.

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

The authoritative key names live in `hermes_station/admin/provider.py` (LLM providers) and `hermes_station/admin/channels.py` (Discord and other channels). When in doubt, those files win.

## First-boot config seeding

On the first start against a fresh `/data`, hermes-station writes a minimal `config.yaml` containing:

- **Holographic memory** provider on by default.
- A curated set of **MCP servers** added but `enabled: false` — they appear in the admin UI ready to toggle on once you've supplied any keys they need.
- A **neutral personality default** (no opinionated system-prompt overlay).
- `display.show_cost: true` so token costs surface in the WebUI.

Seeding is **no-clobber**: any value already present in `config.yaml` wins. Re-running the container against a populated `/data` will not overwrite your settings.

A minimal annotated starter that boots cleanly with zero secrets (degraded but running) is at [`config.example.yaml`](config.example.yaml).

### Provider auto-seed

If you set one of the provider env vars below at first boot, hermes-station writes a matching `model:` block to `config.yaml` automatically — no manual `/admin/settings` step required. The seeder is implemented as `seed_provider_from_env` in `hermes_station/config.py`; the spec is pinned by [`tests/test_config_seed_provider.py`](../tests/test_config_seed_provider.py).

| Env var               | Seeded `model.provider` | Default `model.name`            |
| --------------------- | ----------------------- | ------------------------------- |
| `OPENROUTER_API_KEY`  | `openrouter`            | `anthropic/claude-sonnet-4.5`   |
| `ANTHROPIC_API_KEY`   | `anthropic`             | `claude-sonnet-4-5`             |
| `OPENAI_API_KEY`      | `openai`                | `gpt-4.1`                       |

Rules:

- **Precedence is table order.** If multiple keys are set, the first non-empty one wins (OpenRouter first because it's the template's headline path). Empty / whitespace-only values are treated as unset.
- **No-clobber is absolute.** If `config.yaml` has *any* `model:` block — even a partial one like `model: {name: foo}` with no `provider` — the seeder skips and logs why. Operators who edited the file get to keep their state.
- **Always logs.** One INFO line per boot describing the outcome (seeded / skipped-because-already-set / skipped-because-no-keys / skipped-because-empty), so `railway logs` makes the chosen path obvious.
- **Drift detection.** If `model.provider` is configured but its env var is missing *and* a different provider's key is present, a single WARNING is emitted at boot pointing the operator at `/admin/settings` to switch.

## Build metadata

### `IMAGE_REVISION`

The Dockerfile accepts `--build-arg IMAGE_REVISION=<git-sha>`, defaulting to `${RAILWAY_GIT_COMMIT_SHA:-dev}`. The value is written to `/etc/hermes-station-build` inside the image, attached as `org.opencontainers.image.revision`, and surfaced on `/health` as `versions.image_revision` and `summary.image_revision`.

Three deploy modes:

- **Railway template deploy** — `RAILWAY_GIT_COMMIT_SHA` is set by the builder; the JSON template threads it into the build via the `IMAGE_REVISION` reference variable. `image_revision` ends up as a 7–40 char hex SHA.
- **CI build** — `.github/workflows/ci.yml` passes `--build-arg IMAGE_REVISION=${{ github.sha }}` explicitly.
- **Local `docker build .`** — neither is set, so `IMAGE_REVISION` falls through to `"dev"` and `/health` shows `image_revision: "dev"`. That's expected; pass `--build-arg IMAGE_REVISION=$(git rev-parse HEAD)` if you want the real SHA locally.

## Health surface

Three endpoints (see the README for the exact JSON example):

- `/health/live` — liveness probe, always cheap, 200 while the process is up.
- `/health/ready` — readiness probe, returns `503` when the composite status is `degraded` or `down`.
- `/health` — full JSON, **always 200**, with a top-level `status` of `ok` / `degraded` / `down` and a `readiness` map of per-capability `{ready, reason}` rows.

### The warn-and-continue model

For every capability listed in `config.yaml`, hermes-station performs a startup probe (checks that the required secret is present, the dependency reachable, etc.). The outcomes:

- **All probes pass** → `status: "ok"`, all rows `ready: true`.
- **One or more probes fail because of missing config/secrets** → `status: "degraded"`, the failing rows carry `ready: false` and a human-readable `reason`. **The container does not exit.** Probes are re-run when config changes via the admin UI, so flipping a switch and pasting a key promotes you to `ok` without a restart.
- **A core subsystem (WebUI, gateway, control plane) is dead** → `status: "down"` and `/health/ready` returns `503`.

This is intentional: the most common "is it broken?" failure mode for a freshly-deployed container should be a clear `/health` story, not a crash loop.

## Logs

Stdout is JSON, one object per line. Each record carries at minimum: `ts`, `level`, `component`, `event`, `message`, plus contextual extras (request id, capability name, exception info, etc.).

Useful `jq` filters:

```bash
# Capability readiness checks
container logs hermes-station | jq 'select(.component=="readiness")'

# Gateway / channel runtime (Discord, Telegram, Slack)
container logs hermes-station | jq 'select(.component=="gateway")'

# WebUI subprocess
container logs hermes-station | jq 'select(.component=="webui")'

# HTTP access logs
container logs hermes-station | jq 'select(.component=="http")'

# Everything at warning or above
container logs hermes-station | jq 'select(.level=="warning" or .level=="error")'
```

If you want plain text for grep-driven debugging, pipe through `jq -r '"\(.ts) \(.level) \(.component) \(.message)"'`.
