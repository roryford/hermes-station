# hermes-station: Rewrite Plan

## Background

hermes-station was created as an "all-in-one" container to run hermes-webui and
hermes-agent together in a single deployable unit, with Railway as the primary
target. It was never meant to be a Python application with its own control plane.

Over time, station grew a Python layer (~3,500 lines) to paper over gaps in
hermes-webui: auth, settings management, config seeding, provider detection,
secrets management, log viewing, health reporting. As hermes-webui has matured,
it has absorbed most of these concerns itself. The Python control plane is now
largely redundant — a maintenance burden with no corresponding user value.

This document captures the learnings, reasoning, and target architecture for
collapsing station back to what it was always meant to be: a Dockerfile, an
entrypoint script, and a Railway template.

---

## Key Learnings

### How hermes-webui and hermes-agent actually communicate

They do not communicate over HTTP. hermes-webui imports `run_agent.AIAgent`
directly as a Python library to execute agent turns. Shared state lives entirely
on the filesystem under `$HERMES_HOME`:

- `state.db` — SQLite session/transcript store (both read/write)
- `gateway_state.json` — gateway lifecycle state (gateway writes, webui reads)
- `config.yaml` — agent configuration (webui reads/writes via its settings UI)
- `.env` — credentials (read by both at startup)

There is no HTTP API between webui and agent. The "bridge auth" complexity in
station was station-specific, not inherent to the pairing.

### The gateway is optional

The hermes-agent *gateway* (the long-running process that connects to Discord,
Telegram, Slack, etc.) is only needed for messaging platform bots. For
web-only deployments, the webui runs agent turns itself via the direct Python
import. The gateway is a separate optional process, not the core of hermes-agent.

### hermes-webui is self-sufficient

As of current versions, hermes-webui handles:
- Authentication (PBKDF2-SHA256 passwords, signed session cookies, rate limiting)
- Settings UI (provider, model, MCP servers, personality, tools)
- First-run configuration wizard
- Session management and transcript storage
- Agent streaming (SSE)
- Extension injection (JS/CSS via env vars)
- Its own `/health` endpoint

Station's admin dashboard, config seeding, secrets passthrough, and readiness
validator all duplicate or wrap functionality webui now owns.

### What station legitimately still contributes

1. **Container packaging** — the Dockerfile + base image (chromium, ffmpeg,
   tesseract, node, MCP servers, pinned binaries) is the actual product
2. **Process supervision** — starting webui and optionally the gateway,
   restarting on crash
3. **Hindsight sidecar** — already handled in `hermes-entrypoint.sh`
4. **Hot-patch mechanism** — `HERMES_PATCH_AGENT_VERSION` and
   `HERMES_PATCH_WEBUI_VERSION` let users upgrade components without a full
   image rebuild; worth keeping
5. **Railway template** — one-click deploy with the right variable prompts
6. **Security hardening** — non-root user (`gosu hermes`), read-only site-packages,
   SHA-pinned dependency fetches

---

## Reasoning for the Change

### Why now

hermes-webui has crossed the threshold where maintaining a Python wrapper around
it costs more than it provides. Every webui version bump requires station to
verify that its config seeding, secrets passthrough, and bridge auth still align
with webui's internals — a coupling that never needed to exist.

### Why not a new repo

The repo identity has value: Railway template URL, GitHub stars/forks, existing
deployments. A major version bump (`v1.0.0`) communicates the architectural
break cleanly without stranding users.

### Why not keep the admin panel

The admin panel (settings, secrets, logs, health) was solving the problem of
"user has no terminal access and no way to configure the agent after deploy."
webui's settings UI now solves this for the vast majority of users. Users who
need more (custom MCP servers, provider keys, etc.) can set env vars before
deploying, or use webui's built-in settings.

### Why supervisord over shell or Python

The new entrypoint has one job: start processes, restart them on crash, forward
signals. supervisord handles restart backoff, per-program log routing to stdout
(Railway log capture), and startup ordering via `priority` — all built-in.
A shell supervision loop reimplements these badly. A tiny Python launcher is
reasonable but adds another thing to own. supervisord is one `apt-get` line in
the base image and a declarative `.conf` file.

---

## New Architecture

```
╔══════════════════════════════════════════════════════════════════════╗
║                      hermes-station container                        ║
║                                                                      ║
║  ┌─────────────────────────────────────────────────────────────┐    ║
║  │                     hermes-entrypoint.sh                    │    ║
║  │  chown /data · hot-patch agent/webui · start hindsight      │    ║
║  │  then: exec supervisord (or inline supervision loop)        │    ║
║  └─────────────────────────────────────────────────────────────┘    ║
║           │               │                    │                     ║
║           ▼               ▼                    ▼                     ║
║  ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐         ║
║  │ hermes-webui │  │    gateway     │  │   hindsight     │         ║
║  │   :8787      │  │  (opt-in)      │  │    :8888        │         ║
║  │              │  │                │  │  (opt-in)       │         ║
║  │ UI + auth    │  │ Discord        │  │  memory API     │         ║
║  │ sessions     │  │ Telegram       │  └────────┬────────┘         ║
║  │ settings     │  │ Slack ...      │           │                   ║
║  │ streaming    │  └───────┬────────┘           │                   ║
║  └──────┬───────┘          │                    │                   ║
║         │ python import    │                    │                   ║
║         ▼                  │                    │                   ║
║  ┌──────────────┐          │  read/write shared state               ║
║  │ hermes-agent │          │                    │                   ║
║  │  (library)   │          │                    │                   ║
║  │ LLM calls    │          │                    │                   ║
║  │ tool exec    │          │                    │                   ║
║  │ shell/file   │          │                    │                   ║
║  │ browser      │          │                    │                   ║
║  └──────┬───────┘          │                    │                   ║
║         └──────────────────┴────────────────────┘                   ║
║                            │                                         ║
║                            ▼                                         ║
║  ┌──────────────────────────────────────────────────────────────┐   ║
║  │                $HERMES_HOME  (mounted volume)                │   ║
║  │  config.yaml  .env  state.db  gateway_state.json             │   ║
║  │  workspace/   webui/  plugins/                               │   ║
║  └──────────────────────────────────────────────────────────────┘   ║
║                                                                      ║
║  ── system tools on PATH ──────────────────────────────────────     ║
║     ffmpeg   tesseract   node   chromium                             ║
╚══════════════════════════════════════════════════════════════════════╝
         ▲                    │                       │
    :8787 (HTTP)        bot platforms           LLM provider
    user's browser   Discord/Telegram/Slack   OpenRouter/Anthropic
```

### Process supervision: supervisord

supervisord (under tini as PID 1) manages webui and optionally gateway as
declared programs. hindsight continues to be started by the shell entrypoint
(it already works that way). Key design points:

- `tini` stays as PID 1 (`ENTRYPOINT ["/usr/bin/tini", "--", ...]`) — handles
  zombie reaping; supervisord runs as its child
- `[program:webui]` — `autostart=true`, `autorestart=true`, `priority=10`,
  `user=hermes`, `stdout_logfile=/dev/fd/1`, `stderr_logfile=/dev/fd/2`
- `[program:gateway]` — `autostart=false` (controlled by `HERMES_GATEWAY_ENABLED`
  env var; entrypoint writes `autostart=true` into the conf before exec-ing
  supervisord if the var is set), `priority=20` (starts after webui)
- supervisord's unix socket: `chmod=0700`, `chown=root:hermes` so the hermes
  user can query status without running as root
- `supervisord.conf` is added to `Dockerfile.base` so the runtime stage
  doesn't bust the layer cache

**Startup ordering:** webui must be healthy before gateway starts (SQLite
initialization race — two processes opening a zero-byte `state.db` concurrently
can corrupt the journal). supervisord's `priority` field ensures ordering, but
gateway should also poll webui's `/health` before calling `start_gateway()` —
or the entrypoint polls `/health` before writing gateway's `autostart=true`.

**Gateway heartbeat:** The current in-process `_refresh_updated_at()` task
patches `gateway_state.json` every 30s because webui declares the gateway stale
after 120s. As a subprocess, this needs verification: does `start_gateway()` as
a top-level process write its own `updated_at` heartbeat, or does it rely on the
station supervisor? This must be confirmed before the rewrite ships — if the
subprocess gateway doesn't self-heartbeat, webui will show "disconnected" after
120s even when the gateway is healthy. If needed, a small wrapper script can
touch `gateway_state.json` on a 30s loop alongside the gateway process.

---

## File-by-File Change Plan

### Deleted entirely

| Path | Why |
|---|---|
| `hermes_station/` | The Python control plane — all of it |
| `pyproject.toml` | No Python app to package |
| `uv.lock` | Follows pyproject.toml |
| `extension/` | Admin JS/CSS extension — webui owns extensions now |
| `tests/test_admin_*.py` | Testing deleted admin routes |
| `tests/test_bridge_auth.py` | Station-specific auth bridge |
| `tests/test_config_*.py` | Station config seeding/normalization |
| `tests/test_gateway*.py` | Station Gateway class |
| `tests/test_health.py` | Station health endpoint |
| `tests/test_htmx_*.py` | HTMX admin dashboard |
| `tests/test_lifespan_*.py` | Starlette lifespan |
| `tests/test_logs_json.py` | Station log format |
| `tests/test_mcp_*.py` | Station MCP seeding |
| `tests/test_memory_default.py` | Station memory seeding |
| `tests/test_pilot_*.py` | Admin extension pilot |
| `tests/test_provider.py` | Station provider seeding |
| `tests/test_readiness.py` | Station readiness validator |
| `tests/test_routes.py` | Station route table |
| `tests/test_safer_agent_defaults.py` | Station agent defaults seeding |
| `tests/test_secrets.py` | Station secrets management |
| `tests/test_supervisors.py` | Station supervisor classes |
| `tests/test_webui_env_passthrough.py` | Station env filtering |
| `tests/test_app.py` | Starlette app factory |
| `tests/test_main.py` | Station __main__ |
| `tests/test_smoketest.py` | Admin smoketest route |
| `tests/test_compat*.py` | Station compat layer |
| `tests/test_docs_consistency.py` | Station docs |
| `tests/browser/` | Admin extension browser tests |

### Kept / modified

| Path | Change |
|---|---|
| `Dockerfile` | Remove `pyproject.toml`/`uv.lock` COPY, `hermes_station` install, `extension.tar` ADD, `hermes_station.tar` ADD; set `HERMES_WEBUI_HOST=0.0.0.0`, `HERMES_WEBUI_PORT=8787`, `HERMES_WEBUI_AGENT_DIR` in ENV block; change CMD to supervisord |
| `Dockerfile.base` | Add `supervisor` apt package |
| `scripts/hermes-entrypoint.sh` | Add: `mkdir -p` for data dirs (fix fresh-volume bug), `HERMES_GATEWAY_AUTOSTART` deprecation warning, conditional supervisord conf patching for gateway, then `exec tini -- supervisord` |
| `supervisord.conf` | New: webui + gateway programs, log routing to fd/1+fd/2 |
| `railway-template.json` | Remove `HERMES_ADMIN_PASSWORD` (isOptional:false) and `HERMES_STATION_PILOT_ADMIN_EXTENSION`; add `HERMES_GATEWAY_ENABLED`; republish via Railway dashboard |
| `railway.toml` | Unchanged — webui exposes `/health` at same path |
| `renovate.json5` | Remove hermes_station version regex managers |
| `README.md` | Rewrite: new architecture, "UI vs env vars" config table, no `/admin` references |
| `CLAUDE.md` | Remove "Quick unit + lint run" section; simplify staging dir steps (remove hermes_station.tar, extension.tar lines) |
| `.github/workflows/ci.yml` | Remove: unit job's ruff/mypy/uv install steps; `HERMES_ADMIN_PASSWORD` from boot step; update `/health` jq assertions to match webui schema; remove `uv pip install -e` from e2e step |
| `.github/workflows/release.yml` | Remove hermes_station.tar and extension.tar from pre-pack step |

### New files

| Path | Purpose |
|---|---|
| `supervisord.conf` | Declares webui + gateway programs with restart, log routing, ordering |
| `tests/test_container_smoke.py` | Container boot assertions (see smoke test spec below) |

### Kept tests (still valid)

| Path | Why kept |
|---|---|
| `tests/test_container_toolbelt.py` | Tests agent tools inside container — still relevant |
| `tests/test_plugin_manifests.py` | Tests plugin.yaml patch — still relevant |
| `tests/test_hindsight_sidecar.py` | Tests hindsight sidecar — still relevant |
| `tests/test_hindsight_entrypoint.py` | Tests hindsight env setup — still relevant |
| `tests/test_login_smoke.py` | End-to-end auth smoke — still relevant |
| `tests/test_e2e_webui_contract.py` | Webui API contract — still relevant |
| `tests/test_version.py` | Verify before keeping — currently checks `hermes-station` package version which won't exist; update to check build label or drop |

---

## Environment Variables

### Removed (station-specific, no longer meaningful)

| Variable | Was used for |
|---|---|
| `HERMES_ADMIN_PASSWORD` | Admin dashboard login |
| `HERMES_STATION_PILOT_ADMIN_EXTENSION` | Admin extension feature flag |
| `HERMES_GATEWAY_AUTOSTART` | Station's Gateway class autostart logic |

### Kept / passed through to webui/agent

| Variable | Purpose |
|---|---|
| `HERMES_WEBUI_PASSWORD` | webui auth (webui consumes directly) |
| `OPENROUTER_API_KEY` | LLM provider credential |
| `HERMES_HOME` | Shared data root |
| `HERMES_CONFIG_PATH` | config.yaml path |
| `HERMES_WEBUI_STATE_DIR` | webui session/settings dir |
| `HINDSIGHT_SIDECAR` | Enable hindsight (entrypoint handles) |
| `HERMES_PATCH_AGENT_VERSION` | Hot-patch agent at startup |
| `HERMES_PATCH_WEBUI_VERSION` | Hot-patch webui at startup |

### New (entrypoint-level controls)

| Variable | Purpose |
|---|---|
| `HERMES_GATEWAY_ENABLED` | `1`/`true` to start the gateway process |

### Required additions to Dockerfile ENV block

These must be set at the Dockerfile level — previously station's `webui.py`
set them dynamically before spawning the webui subprocess:

| Variable | Value | Why |
|---|---|---|
| `HERMES_WEBUI_HOST` | `0.0.0.0` | webui previously bound to `127.0.0.1:8788`; must bind publicly now that there is no proxy |
| `HERMES_WEBUI_PORT` | `8787` | matches Railway healthcheck and template domain target |
| `HERMES_WEBUI_AGENT_DIR` | `$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")` baked at build time | webui's agent discovery must find hermes-agent; without this, agent turns silently fail at runtime — a deploy-time blocker |

---

## What We Lose

### For users

| Lost | Severity | Mitigation |
|---|---|---|
| `/admin` dashboard (settings, secrets, logs) | Medium | webui settings UI covers most of this; env vars cover the rest |
| Station health endpoint with readiness detail | Low | webui's `/health` still exists |
| Provider auto-detection and drift warnings | Low | Users set env vars; webui surfaces config state |
| First-boot config seeding (MCP defaults, personality) | Low | webui first-run wizard handles this |
| In-UI log viewer | Low | Railway log viewer covers deployed use; local testing uses stdout |
| Admin extension (status pane in webui) | Low | Was a pilot feature; webui gains its own status UI |

### For developers / maintainers

| Lost | Notes |
|---|---|
| ~3,500 lines of Python to maintain | This is the point |
| ~40 unit test files | Replaced by simpler e2e container tests |
| Fast unit test loop (`uv run pytest -q`, no container) | Real regression — new minimum feedback loop is a full container build (5–10 min locally). No mitigation other than accepting it; the remaining tests are inherently container-level |
| Fine-grained env passthrough filtering | Acceptable — single-tenant container; agent can already exec arbitrary shell |
| Log redaction of secrets | Low risk — verify webui's startup logging doesn't emit env vars; if it does, set log level accordingly |
| `disabled_secrets` suppression (used by Copilot OAuth / GITHUB_TOKEN suppression) | Behavior regression for bot users using Copilot — must be called out in migration guide |
| Signal-handler isolation for in-process gateway | Moot — gateway runs as a subprocess now |
| Security headers middleware (CSP, X-Frame-Options, etc.) | Must audit webui's response headers before shipping; add nginx shim if webui doesn't set them |
| Body size limit (100 MB cap) | Must verify webui enforces an upload cap; if not, document the gap |

---

## Migration Path for Existing Deployments

Existing Railway deployments pull `ghcr.io/roryford/hermes-station:latest`.
When the new image is published:

1. `HERMES_ADMIN_PASSWORD` — harmless if left set (new image ignores it)
2. `HERMES_STATION_PILOT_ADMIN_EXTENSION` — harmless if left set (ignored)
3. `HERMES_GATEWAY_AUTOSTART` — **action required for bot users.** This variable
   is gone. The entrypoint will log a visible warning if it detects the old name.
   Users relying on bot connectivity must set `HERMES_GATEWAY_ENABLED=1`.
   Users who had `HERMES_GATEWAY_AUTOSTART=auto` and were using bots will lose
   connectivity silently after upgrade unless they act on the warning.
4. `GITHUB_TOKEN` suppression — users who previously used the admin UI to suppress
   `GITHUB_TOKEN` (to avoid Copilot OAuth credential pool contamination from
   Railway's auto-injected variable) will lose that suppression. They must either
   remove `GITHUB_TOKEN` from their Railway service variables or wait for webui
   to expose equivalent suppression controls. Call this out prominently in the
   changelog.
5. `/data` volume — fully compatible; config.yaml, state.db, and webui state
   carry over unchanged. No data migration required.

No data migration required. The volume format is owned by hermes-agent and
hermes-webui, not station.

---

## Resolved Questions (from review)

1. **Supervision strategy** → **supervisord** under tini. Add to Dockerfile.base.

2. **Gateway autostart heuristic** → **Explicit opt-in only** (`HERMES_GATEWAY_ENABLED=1`).
   No auto-detection of provider config. Simpler, no YAML parsing in shell.

3. **Health endpoint** → webui's `/health` is sufficient for `railway.toml`.
   Verify the CI jq assertions against webui's actual `/health` response schema
   and update accordingly.

4. **Log handling** → Acceptable for single-tenant. Verify webui's startup logging
   doesn't emit env vars; if it does, adjust log level via env var.

5. **`HERMES_WEBUI_AGENT_DIR`** → **Must be set in Dockerfile ENV block.**
   Bake the site-packages path at build time:
   ```dockerfile
   RUN python3 -c "import sysconfig, os; open('/tmp/agent_dir','w').write(sysconfig.get_paths()['purelib'])"
   ENV HERMES_WEBUI_AGENT_DIR=/usr/lib/python3/dist-packages  # (actual value from build)
   ```
   Or simpler: set it in the entrypoint before exec-ing supervisord:
   ```sh
   export HERMES_WEBUI_AGENT_DIR="$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")"
   ```
   This is a **deploy-time blocker** — missing it means the container boots but
   agent turns fail silently.

6. **Copilot OAuth** → investigate before cutting the branch. The credential pool
   suppression was in `config.py`'s `disabled_secrets` logic. If webui or
   hermes-agent have their own handling, it may not be lost. Needs confirmation.

## Smoke Test Spec (`test_container_smoke.py`)

The replacement test file must assert at minimum:

- `GET /health` returns 200
- `GET /` redirects to login page (auth is active)
- `POST /api/auth/login` with wrong password returns 401
- `POST /api/auth/login` with correct password returns session cookie
- Authenticated `GET /api/sessions` returns 200 (webui session API is live)
- Agent turn: start a session, send a message, receive a streaming response
  (proves `HERMES_WEBUI_AGENT_DIR` is correct and hermes-agent is importable)
- `HERMES_PATCH_AGENT_VERSION` hot-patch: if set, verify installed version matches
- Plugin manifests: webui can load at least one built-in plugin (proxy to
  existing `test_plugin_manifests.py` logic)

## Hot-Patch Behavior Contract

Clarify in entrypoint and README:

- If `HERMES_PATCH_AGENT_VERSION` install fails, **warn and continue** with
  baked version (do not abort the container)
- If `HERMES_PATCH_WEBUI_VERSION` clone fails, **warn and continue** with
  baked webui
- In both cases, log the resolved version prominently after the attempt so
  operators can audit what is actually running
- Log the resolved git SHA after webui clone, not just the tag, since tags
  are mutable

## Pre-existing Bugs Fixed by This Rewrite

- **Fresh volume `mkdir` missing:** The Dockerfile bakes `/data/.hermes`,
  `/data/webui`, `/data/workspace`, `/data/.hindsight` into the image layer, but
  Railway's volume mount at `/data` shadows the image layer on a fresh deploy.
  These dirs don't exist on first boot. Fix: add `mkdir -p` calls at the top of
  `hermes-entrypoint.sh` before chown.

- **Hot-patch vs site-packages chmod:** The current Dockerfile runs
  `chmod -R a-w "$site_pkgs"` after install, hardening site-packages. The
  entrypoint's hot-patch then tries to `uv pip install --system` into those
  read-only dirs and silently fails (or errors). Fix: either remove the chmod
  hardening (acceptable in a single-tenant container where the agent can already
  write anywhere), or run hot-patch before the chmod (not possible since chmod
  is baked into the image). Decision: **remove `chmod -R a-w` from the
  Dockerfile** — it provides no meaningful security in a single-tenant AI agent
  container where tool execution is the explicit purpose.
