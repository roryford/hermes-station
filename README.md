# hermes-station

[Hermes Agent](https://github.com/NousResearch/hermes-agent) is an open-source AI assistant you run on your own infrastructure. You connect it to the LLM provider of your choice and it reaches users over Telegram, Discord, Slack, email, and other channels. hermes-station is the easiest way to self-host it: a single container that bundles the agent and the web chat UI, deployable to Railway or runnable locally with Docker or Apple `container`. Your data and API keys stay on your infrastructure — nothing is routed through a third-party service.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/hermes-station?referralCode=wNX0xW)

## Why it exists

Upstream Hermes requires manual config file editing and SSH access to get started. hermes-station packages the agent with a FIRST RUN wizard so you can configure providers and channels without touching YAML. Set a password, click deploy, open `/` — that's the full onboarding path.

## What this is

A single Railway-deployable container that runs:

- `/` — the Hermes WebUI (chat, settings, provider/channel setup)
- `/health` — healthcheck

Everything writes to `/data` (single Railway volume) and shares one Hermes identity across the WebUI, Telegram, Discord, Slack, and other channels. See `docs/architecture.md` for the full filesystem layout.

## Quick start: Railway

1. Click **Deploy on Railway** above.
2. Set the required env var in the Railway dashboard before the first boot:
   - `HERMES_WEBUI_PASSWORD` — protects the chat UI at `/`
3. Open `/` and use the FIRST RUN wizard to add an LLM provider.

To skip the wizard's provider step, set `OPENROUTER_API_KEY` (or another supported key) as an env var at boot — the auto-seeder writes `model.provider: openrouter` to `config.yaml` on first start.

See [`docs/features.md`](docs/features.md) for the full capability catalogue — LLM providers, channels, voice, memory, web search, browser automation, image generation, observability, and MCP tools. [`docs/configuration.md`](docs/configuration.md) has the full env-var reference.

## Quick start: Docker / Apple container

```bash
mkdir -p /tmp/hermes-station-data

docker run --rm -d --name hermes-station -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=changeme \
  -v /tmp/hermes-station-data:/data \
  ghcr.io/roryford/hermes-station:latest

# Verify it's up
curl http://127.0.0.1:8787/health | jq .status
```

Apple `container` and `docker` are both supported. Then visit `http://127.0.0.1:8787` to finish setup.

## Minimum safe config

Before any non-local deploy, set:

| Variable | Purpose |
|---|---|
| `HERMES_WEBUI_PASSWORD` | Protects the chat UI at `/` |

Without it, the UI is open to anyone who can reach the host. After setting this, capabilities unlock as you add the corresponding provider keys.

## First boot

hermes-station is **warn-and-continue on first boot**: the container starts on an empty `/data` with zero secrets, `/health` reports `ok`, and the FIRST RUN wizard in the WebUI walks you through configuration.

Visit the WebUI to add a provider key, or set `OPENROUTER_API_KEY` (etc.) at boot to skip the manual step. A capability listed in `config.yaml` but missing its secret shows up as `ready: false` with a `reason`; the container does **not** exit.

See [`docs/configuration.md`](docs/configuration.md) for the first-boot config seeding behavior. A minimal starter `config.yaml` lives at [`docs/config.example.yaml`](docs/config.example.yaml).

## Health endpoints

Three endpoints, intended for different consumers:

- `GET /health/live` — process is alive. Cheap; suitable for orchestrator **liveness** probes.
- `GET /health/ready` — composite ready check. Returns `503` when degraded; suitable for orchestrator **readiness** probes.
- `GET /health` — full JSON, **always 200**. The body's `status` field carries the verdict (`ok` / `degraded` / `down`).

Example `/health` body on a fresh boot with **no** `OPENROUTER_API_KEY`:

```json
{
  "status": "ok",
  "components": {
    "webui":         {"state": "ready", "pid": 42},
    "gateway":       {"state": "unknown", "platform": null, "connection": "not_configured"},
    "storage":       {"data_writable": true, "config_readable": true},
    "memory":        {"provider": "holographic", "db_ok": true}
  },
  "readiness": {
    "discord":            {"intended": false, "ready": false},
    "web_search":         {"intended": false, "ready": false},
    "image_gen":          {"intended": false, "ready": false},
    "github":             {"intended": false, "ready": false},
    "memory:holographic": {"intended": true,  "ready": true}
  },
  "versions": {
    "hermes_agent":   "0.x.y",
    "hermes_webui":   "v0.51.x",
    "python":         "3.13.x",
    "image_revision": "dev"
  }
}
```

`status: "ok"` here is correct — no readiness row is `intended: true && ready: false`. `gateway.state: "unknown"` is normal on a fresh boot with no provider configured.

## Volume compatibility

hermes-station is engineered to accept an existing Hermes `/data` volume unchanged. If a deploy is healthy at `/health`, the volume mounts cleanly.

## Known limitations

- **Single-operator deploy** — not a multi-tenant SaaS. One identity, one `/data` volume, one operator.
- **Readiness is a boot-time snapshot** — the `/health/ready` check reflects state at startup, not live probes against upstream APIs.
- **Fast-moving upstreams** — hermes-agent ships weekly; hermes-webui ships several releases per day. Pin versions are tracked by Renovate; don't run `:latest` in production without reviewing the bump PR.
- **Image is intentionally not minimal** — includes Node.js, npm, and the `gh` CLI to support MCP server subprocesses. The image is larger than a stripped runtime.

## Support posture

Single-operator deploy, public repo, best for self-hosters comfortable with Docker or Railway. Issues and PRs welcome; no SLA.

## Advanced / Operator notes

### Upstream tracking

Both upstreams move fast (hermes-agent: weekly, hermes-webui: several releases/day). We pin exact versions and let Renovate open weekly batched bump PRs; CI runs the compat test; auto-merge on green.

- `hermes-agent` — pinned in `Dockerfile` via install args. Tracked by Renovate.
- `hermes-webui` — pinned in `Dockerfile` via `ARG HERMES_WEBUI_VERSION`. Tracked by Renovate's regex manager (`renovate.json5`).

See `renovate.json5` for the schedule and `.github/workflows/ci.yml` for the gate.

### Ops runbook

See [`docs/ops-runbook.md`](docs/ops-runbook.md) for production operational procedures: upgrading a live deployment, backup and restore, migrating to a new Railway project, provider key rotation, and recovering from a bad config.

### Cutting a release

See [`docs/release-runbook.md`](docs/release-runbook.md) for the full flow: version bump, tag, image publish, and the manual Railway redeploy step (with the `--from-source` flag that's a frequent gotcha).

### Version visibility

To see exactly which `hermes-station`, `hermes-agent`, `hermes-webui`, and image revision a deployment is running:

```bash
curl https://your-app/health | jq .versions
```

`image_revision` is the git SHA the image was built from (or `"dev"` for a local `docker build .`).

### Structured logs

Stdout is JSON, one object per line, with `ts`, `level`, `component`, `event`, `message`, and contextual extras. Pipe to `jq` for filtering:

```bash
# Just warnings and errors
container logs hermes-station | jq 'select(.level=="warning" or .level=="error")'
```

## Local development

```bash
# Build image (see CLAUDE.md for the full staging-dir workaround needed with Apple container CLI)
container build -f Dockerfile -t hermes-station:local /tmp/hs-ctx

# Run with a fresh /data
mkdir -p /tmp/hermes-station-data
container run --rm -d --name hermes-station -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=dev \
  --mount type=bind,source=/tmp/hermes-station-data,target=/data \
  hermes-station:local

# Smoke — status: "ok" on first boot is expected (agent is ready, no provider yet)
curl http://127.0.0.1:8787/health | jq .status
```

See [`CLAUDE.md`](CLAUDE.md) for the full local build and test workflow.

## License

MIT — see `LICENSE`. The pinned upstreams (`hermes-agent`, `hermes-webui`) are also MIT.
