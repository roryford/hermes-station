# hermes-station architecture

## Overview

hermes-station is a single-container Railway deployment that packages hermes-webui
(a web chat front-end) and hermes-agent (an AI agent library imported directly by
the webui) into a deployable unit. The container exposes one public port (`$PORT`,
default 8787): `/` serves the chat UI and `/health` is the healthcheck surface.
All agent state persists to `/data` (a single Railway volume), so the process can
restart cleanly against an existing dataset.

There is no Python control plane in this repo — just a Dockerfile, a shell
entrypoint script (`scripts/hermes-entrypoint.sh`), and a supervisord config
(`supervisord.conf`).

---

## Process model

Processes are started and supervised by supervisord (PID 1 via tini).

```
  tini (PID 1)
  └─ supervisord
      ├─ hermes-webui        [python server.py, always started]
      │   └─ handles /, /health, auth, sessions, settings
      │
      ├─ hermes-gateway      [started when HERMES_GATEWAY_ENABLED=1]
      │   └─ messaging bots (Discord, Telegram, Slack) + scheduled/cron jobs
      │
      └─ hindsight           [started when HINDSIGHT_SIDECAR=1]
          └─ local memory API (embedded Postgres via pg0)
```

### hermes-webui

hermes-webui runs as `python server.py` on port `$PORT` (default 8787). It
handles the public HTTP listener, user authentication, chat UI, provider
configuration, settings panels, and the `/health` endpoint.

hermes-agent is imported directly by hermes-webui as a Python library — it is
not a separate process.

### Gateway (optional)

The hermes-agent gateway handles messaging channels (Discord, Telegram, Slack)
and scheduled/cron jobs. Start it by setting `HERMES_GATEWAY_ENABLED=1`.
supervisord restarts it automatically on crash.

### Hindsight sidecar (optional)

Hindsight is a local memory API backed by an embedded Postgres database (pg0).
It runs on port 8888 (loopback by default). Enable it with `HINDSIGHT_SIDECAR=1`.

---

## What lives in /data

```
/data/
├── .hermes/              $HERMES_HOME — agent runtime state
│   ├── config.yaml       provider + model + feature config
│   ├── .env              API keys and secrets (0600)
│   ├── state.db          SQLite agent state (CRITICAL — do not delete)
│   ├── gateway_state.json  gateway lifecycle state
│   ├── memories/         long-term memory (holographic provider)
│   ├── sessions/         agent conversation sessions
│   ├── skills/           built-in + user skills
│   ├── pairing/          channel pairing state (Telegram etc.)
│   └── cron/             scheduled job definitions
├── webui/                $HERMES_WEBUI_STATE_DIR
│   ├── .signing_key      session signing key (CRITICAL — do not delete)
│   └── sessions/         chat session blobs
└── workspace/            $HERMES_WORKSPACE_DIR — user-controlled files
```

---

## Config model

### Files

| Path | Owner | Purpose |
|---|---|---|
| `/data/.hermes/config.yaml` | hermes-webui (read/write), hermes-agent (read) | Provider, model, memory, channels, MCP, toolsets |
| `/data/.hermes/.env` | hermes-webui (read/write), hermes-agent (read) | API keys and channel secrets (mode 0600) |

Both files are written atomically (temp file + `rename()`) and with mode 0600.

### First-boot seeding

On first boot against a fresh `/data`, hermes-webui seeds `config.yaml` with:

- **Holographic memory** provider on by default.
- A curated set of **MCP server** entries with `enabled: false`.
- A **neutral personality** default.
- `display.show_cost: true`.

Seeding is no-clobber: any value already present in `config.yaml` wins.

If `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY` is set at
first boot, a matching `model:` block is written to `config.yaml` automatically.

---

## Health endpoint

`GET /health` is served by hermes-webui and always returns 200. The body's
`status` field carries the verdict (`ok` / `degraded` / `down`).

Railway's healthcheck is configured in `railway.toml` to use `GET /health`.

---

## Security model

### Non-root user

The container entrypoint runs as root only long enough to `chown -R 10000 /data`,
then drops to the `hermes` user (uid 10000) via `gosu`. All subsequent code runs
as the unprivileged `hermes` user.

### Read-only app and site-packages

During the image build, after all packages are installed:

```
chmod -R a-w <site-packages> /opt/hermes-webui
```

This makes Python's site-packages and the hermes-webui source non-writable for
the runtime `hermes` user. Only `/data` and `/opt/mcp-cache` remain writable.

### MCP binary placement

MCP servers (filesystem, GitHub, fetch) are pre-cached during the image build.
They are installed globally by `npm` and `uv tool install` at build time and
invoked read-only at runtime.
