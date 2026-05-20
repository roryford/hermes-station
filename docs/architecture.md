# hermes-station architecture

## Overview

hermes-station is a single-container Railway deployment that wraps hermes-agent
(an open-source AI assistant) together with hermes-webui (a web chat front-end)
and a browser-based control plane. The container exposes one public port (`$PORT`,
default 8787): `/` serves the chat UI, `/admin` is the setup and management
control plane, and `/health` is the healthcheck surface. All agent state persists
to `/data` (a single Railway volume), so the process can restart cleanly against
an existing dataset.

---

## Process model

Three workloads share a single uvicorn event loop. They are started and stopped
by the ASGI lifespan handler in `hermes_station/app.py`.

```
  uvicorn process (PID 1 via tini + gosu)
  ├─ Starlette app  [control plane — ASGI, same event loop]
  │   ├─ /admin/*   HTMX dashboard routes
  │   ├─ /health/*  health endpoints
  │   └─ /*         HTTP proxy → hermes-webui
  │
  ├─ asyncio Task: gateway supervisor  [Gateway._supervise]
  │   └─ asyncio Task: start_gateway()  [hermes-agent in-process]
  │
  └─ asyncio subprocess: hermes-webui  [WebUIProcess._supervise]
      └─ python server.py  (stdlib http.server, port 8788)
```

### Control plane

The control plane is a Starlette ASGI application run by uvicorn. It owns the
public listener, handles all `/admin` and `/health` requests directly, and
reverse-proxies everything else to the hermes-webui subprocess. The lifespan
handler (`app.py:lifespan`) is the single authoritative startup/shutdown
coordinator: it creates the two shared httpx clients, seeds config, validates
readiness, conditionally starts the gateway, then starts hermes-webui. On SIGTERM
it stops both workloads before returning.

### hermes-webui subprocess (`hermes_station/webui.py`)

hermes-webui is not ASGI-mountable (it is hand-rolled on stdlib `http.server`),
so it runs as a supervised child process via `asyncio.create_subprocess_exec`.
It binds on loopback port 8788. The control plane proxies public traffic to it
over HTTP using a pooled `httpx.AsyncClient`.

`WebUIProcess` owns two asyncio tasks:

- **Supervisor** (`_supervise`): waits for the process to exit; if it exits
  unexpectedly, respawns it after an exponential backoff starting at 1 s and
  capped at 30 s. The supervisor runs until `stop()` is called.
- **Log pump** (`_pump_logs`): reads the subprocess's combined stdout/stderr
  line by line, redacts secrets matching a known pattern, emits each line
  through the structured logger (so it appears in Railway's log UI), and appends
  it to the in-memory `WEBUI_LOGS` ring buffer used by the admin Logs page.

If the hermes-webui source is absent at boot, `WebUIProcess.mark_disabled()` is
called and the supervisor never starts; `/` returns 502 for all requests.

### Gateway asyncio task (`hermes_station/gateway.py`)

The hermes-agent gateway is an async-native coroutine (`gateway.run.start_gateway`)
that runs as a supervised `asyncio.Task` in the same event loop as uvicorn.

`Gateway` owns two asyncio tasks:

- **Supervisor** (`_supervise`): wraps `start_gateway()` in a loop; on an
  unexpected exit it increments a crash counter and retries after exponential
  backoff (base 5 s, cap 60 s). A clean exit (`ok=True`) stops the loop without
  restarting. The supervisor is cancelled on `stop()`.
- **Heartbeat** (`_refresh_updated_at`): every 30 s, if `gateway_state.json`
  shows `gateway_state == "running"`, it rewrites `updated_at` with the current
  UTC timestamp. This keeps hermes-webui's stale-gateway check (which flags
  states older than 120 s) from triggering in the in-process deployment model.

**Signal handling:** `start_gateway` registers its own `SIGINT`/`SIGTERM`
handlers via `loop.add_signal_handler`. The supervisor temporarily replaces
`loop.add_signal_handler` with a no-op for the duration of each `_run_once()`
call and restores it afterward, preventing the gateway from clobbering uvicorn's
own signal handling.

**Autostart:** `should_autostart()` is evaluated at lifespan startup. With the
default `HERMES_GATEWAY_AUTOSTART=auto` the gateway starts only when a provider
is configured in `config.yaml` and the corresponding API key is set. Explicit
`on`/`off` overrides the logic unconditionally.

### Crash behavior summary

| Component | Supervised by | Crash behavior |
|---|---|---|
| Control plane | uvicorn / Railway restart policy | `ON_FAILURE`, 10 retries |
| hermes-webui | `WebUIProcess._supervise` | Respawn with backoff (1 s → 30 s) |
| Gateway | `Gateway._supervise` | Respawn with backoff (5 s → 60 s); clean exit does not restart |

---

## Request flow

```
  Browser / curl
       |
       | HTTPS (Railway TLS termination)
       v
  :8787  uvicorn  [_BodySizeLimitMiddleware → _SecurityHeadersMiddleware → router]
       |
       +--/health/live, /health/ready, /health
       |      handled by hermes_station/health.py
       |
       +--/admin/*, /admin/api/*
       |      handled by Starlette routes (HTMX templates / JSON API)
       |      auth guard: session cookie (hermes_station_admin)
       |
       +--/* (everything else)
              proxy_to_webui() [hermes_station/proxy.py]
                  |
                  | HTTP/1.1  (loopback, keep-alive pool)
                  v
             127.0.0.1:8788  hermes-webui subprocess
                  |
                  | StreamingResponse (aiter_raw — preserves Content-Encoding)
                  v
             browser receives response
```

**Proxy details** (`hermes_station/proxy.py`):

- Hop-by-hop headers (`connection`, `transfer-encoding`, etc.) are stripped in
  both directions per RFC 7230.
- The `hermes_station_admin` session cookie is stripped before forwarding so the
  admin credential never reaches hermes-webui.
- Client-injected `X-Forwarded-*` / `X-Real-*` headers are stripped; the proxy
  re-injects them from trusted sources (`request.url`, `request.headers["host"]`)
  so hermes-webui's CSRF check sees the public hostname instead of the loopback.
- Responses are streamed with `aiter_raw()` (raw bytes, not decoded) so
  `Content-Encoding: gzip` SSE streams work end-to-end without re-encoding.
- `Content-Length` is stripped from responses to allow chunked streaming.
- A `RemoteProtocolError` (stale keep-alive connection) triggers one automatic
  retry; other errors return 502.

---

## Config model

### Files

| Path | Owner | Purpose |
|---|---|---|
| `/data/.hermes/config.yaml` | control plane (read/write), hermes-agent (read) | Provider, model, memory, channels, MCP, toolsets |
| `/data/.hermes/.env` | control plane (read/write), hermes-agent (read) | API keys and channel secrets (mode 0600) |

Both files are written atomically (temp file + `rename()`) and with mode 0600.
The formats are documented in `docs/CONTRACT.md` §4.1–4.2 and are stable across
hermes-station and standalone hermes-agent deployments — an existing `/data`
volume mounts cleanly without migration.

### Seeding (first-boot, no-clobber)

On every boot, `lifespan` runs a sequence of seeding functions before loading
config. All seeds are no-clobber: if the target key already exists in
`config.yaml`, the existing value wins and the seeder returns `False`.

1. **Memory provider** (`seed_default_memory_provider`): sets
   `memory.provider: holographic` if no memory provider is configured.
2. **MCP servers** (`seed_default_mcp_servers`): adds a curated set of MCP
   server entries with `enabled: false` if they are absent.
3. **Personality** (`seed_neutral_personality_default`): sets a neutral
   personality default if none is configured.
4. **Show cost** (`seed_show_cost_default`): sets `display.show_cost: true` if
   not configured.
5. **Provider auto-seed** (`seed_provider_from_env`): if `config.yaml` has no
   `model:` block at all, inspects the process environment for
   `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY` (in that
   order) and writes a matching `model: {provider, default}` block. Logs the
   outcome on every boot.

After seeding, `load_yaml_config` + `normalize_config` reconcile any structural
drift introduced by earlier versions, writing the result back if changes were
needed.

### Environment precedence

The `.env` file takes precedence over Railway-injected env vars (CONTRACT.md
§2.1). After config is loaded, `seed_env_file_to_os` merges `.env` values into
`os.environ`, overwriting any existing keys. This means secrets stored via
`/admin` override Railway dashboard variables without requiring a redeploy.

`PYTHONPATH`, `PYTHONSTARTUP`, `LD_PRELOAD`, and `LD_LIBRARY_PATH` are blocked
from the `.env` merge to prevent code-injection through an agent-writable file.

Keys listed under `admin.disabled_secrets` in `config.yaml` are popped from
`os.environ` after the merge, so an operator can actively suppress a
Railway-injected secret (e.g. hide `FAL_KEY` to disable image generation)
without touching the Railway dashboard.

### What lives in /data

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

## Health and readiness model

Three endpoints serve different consumers (`hermes_station/health.py`):

| Endpoint | HTTP status | Use case |
|---|---|---|
| `GET /health/live` | Always 200 | Orchestrator liveness probe — does not touch subprocess state |
| `GET /health/ready` | 200 ok / 503 degraded or down | Orchestrator readiness probe |
| `GET /health` | Always 200 | Dashboard / monitoring — read `status` field in body |

The full payload from `/health` and `/health/ready` includes:

- `status`: `ok` | `degraded` | `down`
- `components.control_plane`, `.webui`, `.gateway`, `.scheduler`, `.storage`, `.memory`
- `readiness`: per-capability rows (see below)
- `versions`: hermes-station, hermes-agent, hermes-webui, Python, image revision

### Readiness rows

`validate_readiness` (`hermes_station/readiness.py`) runs once at boot and
produces a `Readiness` object cached on `app.state.readiness`. Each capability
is a `CapabilityRow`:

```
intended: bool   # operator has configured this capability
ready:    bool   # the required secret / path is actually present
reason:   str    # human-readable explanation when ready=false
source:   str    # "env_file" | "process_env" | "absent"
```

Capabilities checked: primary model provider, delegation provider(s), Discord
and other channels, web search backend, image generation (`FAL_KEY`), GitHub
MCP (`GITHUB_TOKEN`), holographic memory.

**Warn-and-continue**: a capability that is intended but not ready (e.g. a
provider key is missing) sets `ready: false` and causes `/health` to report
`status: "degraded"`. The container does **not** exit. This design keeps the
image shareable — the default posture is a running process that explains what
is missing, not a crash.

`status: "down"` is reserved for when `/data` is not writable — that is the
only condition that prevents any useful work.

---

## Security model

### Non-root user

The container entrypoint (`hermes-entrypoint`) runs as root only long enough to
`chown -R 10000 /data` (so the bind-mounted volume is accessible regardless of
the volume's existing ownership), then drops to the `hermes` user (uid 10000)
via `gosu`. All subsequent code runs as the unprivileged `hermes` user.

### Read-only app and site-packages

During the image build, after all packages are installed:

```
chmod -R a-w <site-packages> /opt/hermes-webui /app
```

This makes Python's site-packages, the hermes-webui source, and the station
source tree (`/app`) non-writable for the runtime `hermes` user. Code that runs
in the container cannot modify its own interpreter, libraries, or application
source. Only `/data` and `/opt/mcp-cache` remain writable.

### MCP binary placement

MCP servers (filesystem, GitHub, fetch) are pre-cached during the image build
under `/opt/mcp-cache` (owned by `hermes`, mode `a+rX`). They are installed
globally by `npm` and `uv tool install` at build time and invoked read-only at
runtime. Executing MCP binaries from a writable cache directory (e.g. `~/.npm`)
would allow the agent to overwrite its own tools; `/opt/mcp-cache` is writable
only by the `hermes` user and its subdirectory layout is set by the build, not
the runtime.

### HTTP security headers

`_SecurityHeadersMiddleware` injects the following on every response:

- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `X-XSS-Protection: 0` (disabled in favor of CSP)
- `Content-Security-Policy`: restricts script/style/connect sources to `'self'`
  with `unpkg.com` allowed for scripts; `frame-ancestors 'none'`

### Body size limit

`_BodySizeLimitMiddleware` rejects any request body larger than 100 MB (fast-
path on `Content-Length`; slow-path byte counter for chunked encoding) with 413.

---

## Admin UI

### What /admin does

`/admin` is a browser-based control plane for hermes-station. It lets an operator:

- Set the LLM provider and API key
- Configure messaging channels (Discord, Telegram, etc.)
- Start, stop, and restart the gateway and webui supervisors
- View live logs (ring buffer of recent webui and gateway output)
- Manage secrets (add, override, or disable env vars)
- Run smoke tests
- Check upgrade status
- Manage agent presets

Admin routes are registered in `hermes_station/admin/routes.py` and the HTMX
dashboard modules (`htmx_dashboard.py`, `htmx_settings.py`, `htmx_logs.py`,
`presets.py`, `smoketest.py`, `upgrade.py`).

### HTMX architecture

The admin UI is rendered server-side with Jinja2 templates and driven by HTMX.
Partial HTML fragments are returned from endpoint handlers; HTMX swaps them into
the page without a full reload. Static assets (JS, CSS) are served from
`/admin/static` via Starlette's `StaticFiles` mount.

There is no JavaScript SPA framework. The only client-side JS is HTMX (loaded
from unpkg.com) plus the station's own inline scripts.

### Session auth

`/admin` is protected by a password defined in `HERMES_ADMIN_PASSWORD` (falls
back to `HERMES_WEBUI_PASSWORD` if unset; if both are unset, `/admin` is open).

Login is a standard HTML form POST to `/admin/login`. On success, the control
plane issues a signed session cookie (`hermes_station_admin`) with a configurable
TTL (`HERMES_ADMIN_SESSION_TTL`, default 86400 s). Every protected route checks
the cookie via `require_admin()`.

Login attempts are rate-limited by client IP: 10 attempts per 60-second window;
excess attempts return 429.

A dual-cookie path also exists for the pilot admin extension: the
`verify_webui_session` bridge verifies a hermes-webui `hermes_session` cookie by
forwarding it to webui's `/api/auth/status` endpoint (loopback, 2 s timeout).
This lets the extension embedded in the webui UI call station APIs without a
separate login.

The `hermes_station_admin` cookie is stripped from all proxied requests to
hermes-webui (`proxy.py:_strip_our_cookies`) so the admin credential never
leaks to the upstream subprocess.
