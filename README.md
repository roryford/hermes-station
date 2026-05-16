# hermes-station

[Hermes Agent](https://github.com/NousResearch/hermes-agent) is an open-source AI assistant you run on your own infrastructure. You connect it to the LLM provider of your choice and it reaches users over Telegram, Discord, Slack, email, and other channels. hermes-station is the easiest way to self-host it: a single container that bundles the agent, the web chat UI, and a browser-based setup wizard, deployable to Railway or runnable locally with Docker or Apple `container`. Your data and API keys stay on your infrastructure — nothing is routed through a third-party service.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/hermes-station?referralCode=wNX0xW)

## Why it exists

Upstream Hermes requires manual config file editing and SSH access to get started. hermes-station packages the agent with a browser-based `/admin` setup wizard so you can configure providers and channels without touching YAML. Set two passwords, click deploy, open `/admin` — that's the full onboarding path.

## What this is

A single Railway-deployable container that runs:

- `/` — the Hermes WebUI
- `/admin` — control plane: browser-based provider/channel setup, gateway controls, logs
- `/health` — healthcheck

Everything writes to `/data` (single Railway volume) and shares one Hermes identity across WebUI, Telegram, Discord, Slack, and other channels. See `docs/CONTRACT.md` §3 for the full filesystem layout.

![Admin dashboard](docs/screenshots/admin-dashboard.png)

![Admin settings](docs/screenshots/admin-settings.png)

## Quick start: Railway

1. Click **Deploy on Railway** above.
2. Set two required env vars in the Railway dashboard before the first boot:
   - `HERMES_WEBUI_PASSWORD` — protects the chat UI at `/`
   - `HERMES_ADMIN_PASSWORD` — protects the control plane at `/admin`
3. Open `/admin` and use the setup wizard to add an LLM provider.

To skip the `/admin` provider step, set `OPENROUTER_API_KEY` (or another supported key) as an env var at boot — the auto-seeder writes `model.provider: openrouter` to `config.yaml` on first start.

See [`docs/configuration.md`](docs/configuration.md) for the full env-var reference.

## Quick start: Docker / Apple container

```bash
mkdir -p /tmp/hermes-station-data

docker run --rm -d --name hermes-station -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=changeme \
  -e HERMES_ADMIN_PASSWORD=changeme \
  -v /tmp/hermes-station-data:/data \
  ghcr.io/roryford/hermes-station:latest

# Verify it's up
curl http://127.0.0.1:8787/health | jq .status
```

Apple `container` and `docker` are both supported — commands are compatible enough for the run flow used here. Then visit `http://127.0.0.1:8787/admin` to finish setup.

## Minimum safe config

Before any non-local deploy, set both of these:

| Variable | Purpose |
|---|---|
| `HERMES_WEBUI_PASSWORD` | Protects the chat UI at `/` |
| `HERMES_ADMIN_PASSWORD` | Protects the control plane at `/admin` |

Without them, both UIs are open to anyone who can reach the host. After setting these two, capabilities unlock as you add the corresponding provider keys (via `/admin` or env vars).

## First boot

hermes-station is **warn-and-continue on first boot**: the container starts on an empty `/data` with zero secrets, `/health` reports `ok` (all intended capabilities are ready; the gateway is idle pending a provider key), and the FIRST RUN wizard in the WebUI walks you through configuration. `status: "ok"` on first boot is not a crash — it means nothing is misconfigured yet. Nothing is required to get a running process.

Visit `/admin` to add a provider key, or set `OPENROUTER_API_KEY` (etc.) at boot to skip the manual step. A capability listed in `config.yaml` but missing its secret shows up as `ready: false` with a `reason`; the container does **not** exit.

See [`docs/configuration.md`](docs/configuration.md) for the first-boot config seeding behavior and the warn-and-continue capability model. A minimal starter `config.yaml` lives at [`docs/config.example.yaml`](docs/config.example.yaml).

## Health endpoints

Three endpoints, intended for different consumers:

- `GET /health/live` — process is alive. Cheap; suitable for orchestrator **liveness** probes.
- `GET /health/ready` — composite ready check. Returns `503` when degraded; suitable for orchestrator **readiness** probes.
- `GET /health` — full JSON, **always 200**. The body's `status` field carries the verdict (`ok` / `degraded` / `down`) so dashboards can read it without treating non-2xx as fatal.

Example `/health` body on a fresh boot with `HERMES_ADMIN_PASSWORD` set and **no** `OPENROUTER_API_KEY` — the auto-seeder finds nothing to seed, so no `provider:*` row appears:

```json
{
  "status": "ok",
  "components": {
    "control_plane": {"state": "ready"},
    "webui":         {"state": "ready", "pid": 42},
    "gateway":       {"state": "unknown", "platform": null, "connection": "not_configured"},
    "scheduler":     {"state": "unknown", "enabled": false, "job_count": null, "last_run_at": null, "failed_jobs": null},
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
    "hermes_station": "0.1.x",
    "hermes_agent":   "0.x.y",
    "hermes_webui":   "v0.51.x",
    "python":         "3.12.x",
    "image_revision": "dev"
  },
  "boot_at": "2026-05-15T12:34:56+00:00",
  "summary": {
    "image_revision": "dev",
    "hermes_agent":   "0.x.y",
    "hermes_webui":   "v0.51.x",
    "python":         "3.12.x",
    "platforms":      [],
    "toolsets":       []
  }
}
```

`status: "ok"` here is correct — no readiness row is `intended: true && ready: false`. The gateway is idle pending a provider key; that is not a misconfiguration.

Same fresh boot **with `OPENROUTER_API_KEY` set** — the seeder writes `model.provider: openrouter` to `config.yaml` on first start, so a `provider:openrouter` readiness row appears and `status` flips to `ok`:

```json
{
  "status": "ok",
  "components": {
    "control_plane": {"state": "ready"},
    "webui":         {"state": "ready", "pid": 42},
    "gateway":       {"state": "running", "platform": null, "connection": "connected"},
    "scheduler":     {"state": "unknown", "enabled": false, "job_count": null, "last_run_at": null, "failed_jobs": null},
    "storage":       {"data_writable": true, "config_readable": true},
    "memory":        {"provider": "holographic", "db_ok": true}
  },
  "readiness": {
    "discord":             {"intended": false, "ready": false},
    "provider:openrouter": {"intended": true,  "ready": true,  "source": "process_env"},
    "web_search":          {"intended": false, "ready": false},
    "image_gen":           {"intended": false, "ready": false},
    "github":              {"intended": false, "ready": false},
    "memory:holographic":  {"intended": true,  "ready": true}
  },
  "versions": {
    "hermes_station": "0.1.x",
    "hermes_agent":   "0.x.y",
    "hermes_webui":   "v0.51.x",
    "python":         "3.12.x",
    "image_revision": "a1b2c3d4e5f6789012345678901234567890abcd"
  },
  "boot_at": "2026-05-15T12:34:56+00:00",
  "summary": {
    "image_revision": "a1b2c3d4e5f6789012345678901234567890abcd",
    "hermes_agent":   "0.x.y",
    "hermes_webui":   "v0.51.x",
    "python":         "3.12.x",
    "platforms":      [],
    "toolsets":       []
  }
}
```

The exact seeder behavior (precedence, default models, no-clobber) is documented in [`docs/configuration.md`](docs/configuration.md#provider-auto-seed) and pinned by [`tests/test_config_seed_provider.py`](tests/test_config_seed_provider.py).

## Volume compatibility

hermes-station is engineered to accept an existing Hermes `/data` volume unchanged. The CI compat test (`tests/test_compat.py`) boots the container against a fixture `/data` snapshot and asserts the contract holds. If that test is green for a given upstream version combo, the image is a verified drop-in.

## Known limitations

- **Single-operator deploy** — not a multi-tenant SaaS control plane. One identity, one `/data` volume, one operator.
- **Readiness is a boot-time snapshot** — the `/health/ready` check reflects state at startup, not live probes against upstream APIs.
- **Fast-moving upstreams** — hermes-agent ships weekly; hermes-webui ships several releases per day. Pin versions are tracked by Renovate; don't run `:latest` in production without reviewing the bump PR.
- **Image is intentionally not minimal** — includes Node.js, npm, and the `gh` CLI to support MCP servers. The image is larger than a stripped runtime.

## Support posture

Single-operator deploy, public repo, best for self-hosters comfortable with Docker or Railway. The browser setup flow reduces YAML editing but provider complexity (API keys, channel tokens, webhook URLs) remains. Issues and PRs welcome; no SLA.

## Advanced / Operator notes

### Upstream tracking

Both upstreams move fast (hermes-agent: weekly, hermes-webui: several releases/day). We pin exact versions and let Renovate open weekly batched bump PRs; CI runs the compat test; auto-merge on green.

- `hermes-agent` — pinned in `pyproject.toml` via `git+https://...@<tag>`. Tracked by Renovate's PEP 621 manager.
- `hermes-webui` — pinned in `Dockerfile` via `ARG HERMES_WEBUI_VERSION`. Tracked by Renovate's regex manager (`renovate.json5`).

See `renovate.json5` for the schedule and `.github/workflows/ci.yml` for the gate.

### Version visibility

To see exactly which `hermes-station`, `hermes-agent`, `hermes-webui`, and image revision a deployment is running:

```bash
curl https://your-app/health | jq .versions
```

`image_revision` is the git SHA the image was built from (or `"dev"` for a local `docker build .`).

### Structured logs

Stdout is JSON, one object per line, with `ts`, `level`, `component`, `event`, `message`, and contextual extras. Pipe to `jq` for filtering:

```bash
# Readiness checks only
container logs hermes-station | jq 'select(.component=="readiness")'

# Just warnings and errors
container logs hermes-station | jq 'select(.level=="warning" or .level=="error")'
```

## Local development

```bash
# Bootstrap (installs app + dev deps; run once after cloning)
uv sync

# Run unit tests
uv run pytest -q

# Build image
docker build -t hermes-station:local .
# (or `container build` — Apple's container CLI is a drop-in)

# Run with a fresh /data
mkdir -p /tmp/hermes-station-data
docker run --rm -d --name hermes-station -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=dev -e HERMES_ADMIN_PASSWORD=dev \
  -v /tmp/hermes-station-data:/data \
  hermes-station:local

# Smoke — status: "ok" on first boot is expected (agent is ready, no provider yet)
curl http://127.0.0.1:8787/health | jq .status
```

## License

MIT — see `LICENSE`. The pinned upstreams (`hermes-agent`, `hermes-webui`) are also MIT.
