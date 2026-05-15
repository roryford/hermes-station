# hermes-station data contract

> **Purpose:** The compatibility contract that hermes-station honors for existing Hermes `/data` volumes, ensuring they mount cleanly with no migration.
>
> **Provenance:** Derived from the Hermes data contract. The compat test (`tests/test_compat.py`) is the executable form of this document — whenever this doc and the test disagree, the test wins and the doc gets updated.

---

## 1. Runtime contract

Held invariant across Hermes deployments.

| Property | Value | Source |
|---|---|---|
| Container public port | `$PORT` (Railway-injected, default `8787`) | `Dockerfile` ENV |
| Public bind host | `0.0.0.0` (settable via `CONTROL_PLANE_HOST`) | `Dockerfile` ENV |
| Healthcheck endpoint | `GET /health` → `200` | `railway.toml`, `control_plane/server.py:385` |
| Volume mount path | `/data` (single mount, single attach) | `Dockerfile` ENV, `railway.toml` |
| `$HOME` inside container | `/data` | `Dockerfile` ENV |
| Restart policy | `ON_FAILURE`, 10 retries | `railway.toml` |

**Signals:** the container must respond to `SIGTERM` with graceful shutdown of all child workloads (WebUI, gateway) before exit. Today this is hand-rolled in the subprocess managers; in hermes-station, in-process workloads make this automatic via the ASGI lifespan handler.

---

## 2. Environment variables

### 2.1 Inbound — set by user via Railway dashboard

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PORT` | yes (Railway-injected) | `8787` | Public listener port |
| `HERMES_WEBUI_PASSWORD` | yes | _(empty = WebUI lockdown)_ | WebUI login |
| `HERMES_ADMIN_PASSWORD` | no (recommended) | falls back to `HERMES_WEBUI_PASSWORD` | `/admin` login |
| `HERMES_ADMIN_USERNAME` | no | `admin` | Not yet implemented — single-password auth only; reserved for future use |
| `HERMES_GATEWAY_AUTOSTART` | no | `auto` | `auto` \| `1`/`true`/`on` \| `0`/`false`/`off` |
| `HERMES_ADMIN_SESSION_TTL` | no | `86400` (seconds) | Admin session lifetime |
| `CONTROL_PLANE_HOST` | no | `0.0.0.0` | Public bind host |

Channel secrets and provider keys are typically managed via `/admin` (which writes them to `$HERMES_HOME/.env`), but **any of them may also be set as Railway env vars** — `os.environ` takes precedence over the `.env` file at process boot in well-behaved Python code (hermes-agent uses `python-dotenv` style loading). This is the basis for the Option C (layered, env-wins) secrets model in hermes-station.

See `provider_catalog()` in `control_plane/config.py:336` for the supported provider env-var names.

### 2.2 Internal — set by the container at boot

These are not part of the user-facing contract (hermes-station may set them differently), but hermes-agent/hermes-webui rely on them being set:

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_HOME` | `/data/.hermes` | Root of agent state |
| `HERMES_CONFIG_PATH` | `$HERMES_HOME/config.yaml` | Provider + model config |
| `HERMES_WEBUI_STATE_DIR` | `/data/webui` | WebUI state (sessions, signing key) |
| `HERMES_WEBUI_AGENT_DIR` | `/app/vendor/hermes-agent` | Path WebUI uses to find agent code (will change in hermes-station — pip install path) |
| `HERMES_WORKSPACE_DIR` | `/data/workspace` | User workspace dir |
| `HOME` | `/data` | So `~` resolves on the volume |
| `PYTHONUNBUFFERED` | `1` | Live log streaming |
| `CONTROL_PLANE_INTERNAL_WEBUI_HOST` | `127.0.0.1` | _Implementation detail — eliminated in hermes-station (in-process mount)_ |
| `CONTROL_PLANE_INTERNAL_WEBUI_PORT` | `8788` | _Implementation detail — eliminated in hermes-station_ |

---

## 3. Filesystem layout under `/data`

Captured from a fresh boot of the baseline image with no prior state.

### 3.1 Directory tree (post-boot, after `/admin` provider+channel save and gateway autostart)

```
/data/
├── .hermes/                         # = $HERMES_HOME
│   ├── SOUL.md                      # seeded from upstream; 0600
│   ├── .env                         # written by /admin save; 0600
│   ├── config.yaml                  # written by /admin save; 0600
│   ├── state.db                     # SQLite, agent state DB (created on first gateway run)
│   ├── gateway_state.json           # gateway runtime state (0600)
│   ├── gateway.lock                 # single-instance lockfile (0644)
│   ├── bin/                         # helper binaries staged by hermes-agent
│   ├── cron/                        # cron job state
│   ├── logs/
│   │   └── curator/                 # agent-side log streams
│   ├── memories/                    # long-term memory store
│   ├── optional-skills/             # opt-in skill catalog (seeded from upstream)
│   ├── pairing/
│   │   ├── _rate_limits.json        # 0600
│   │   ├── telegram-approved.json   # 0600
│   │   └── telegram-pending.json    # 0600
│   ├── sessions/                    # agent sessions
│   └── skills/                      # built-in + user skills (seeded from upstream)
│       └── index-cache/             # JSON caches of external skill indexes
├── webui/                           # = $HERMES_WEBUI_STATE_DIR
│   ├── .signing_key                 # 32 bytes, 0600 — see §3.5
│   └── sessions/                    # chat session blobs
└── workspace/                       # = $HERMES_WORKSPACE_DIR; user-controlled
```

### 3.2 Ownership matrix

| Path | Owner (writer) | hermes-station contract |
|---|---|---|
| `/data/.hermes/.env` | **control plane** | Must read AND write same format (§4.1) |
| `/data/.hermes/config.yaml` | **control plane** | Must read AND write same format (§4.2) |
| `/data/.hermes/pairing/*.json` | hermes-agent (writer); control plane reads + can revoke | Must preserve byte-for-byte; format §4.3 |
| `/data/.hermes/SOUL.md` | hermes-agent (seeded), user-editable | Preserve verbatim |
| `/data/.hermes/state.db` | hermes-agent | **CRITICAL — preserve verbatim.** Losing this wipes agent memory/conversation state. |
| `/data/.hermes/gateway_state.json` | hermes-agent (gateway) | Preserve verbatim |
| `/data/.hermes/gateway.lock` | hermes-agent (gateway) | Transient; safe to leave or remove on boot |
| `/data/.hermes/bin/`, `cron/`, `logs/`, `memories/`, `sessions/` | hermes-agent | Preserve verbatim |
| `/data/.hermes/skills/`, `optional-skills/` | hermes-agent (seeded); user edits preserved | Seed with `no-clobber` on first boot only |
| `/data/webui/.signing_key` | hermes-webui | **CRITICAL — preserve verbatim.** §3.5 |
| `/data/webui/sessions/` | hermes-webui | Preserve verbatim |
| `/data/workspace/` | user | Preserve verbatim |

### 3.3 First-boot seeding behavior

On first boot with an empty `/data`, the container creates the directory skeleton above and seeds these files from the installed agent/webui code:

- `SOUL.md`, `skills/**`, `optional-skills/**` — copied from upstream agent package
- `pairing/*.json` — seeded with `{}` if missing
- `.signing_key` — generated by hermes-webui on first start

On subsequent boots, existing files are **never** clobbered (`cp -rn` semantics for the seeded trees; explicit `[ -s ]` check for pairing files). hermes-station must preserve this no-clobber invariant.

### 3.4 Skill bootstrap path (changes in hermes-station)

Today: `start.sh:34-39` does `cp -rn /app/vendor/hermes-agent/skills/. /data/.hermes/skills/` to seed skills on first boot, and the same for `optional-skills/`.

In hermes-station: since `hermes-agent` is pip-installed (no `vendor/`), the source path becomes the installed package's data dir — `importlib.resources.files("hermes_agent") / "skills"` or equivalent. Functionally identical, but the implementation differs.

### 3.5 The signing_key invariant

`/data/webui/.signing_key` is a 32-byte secret used by hermes-webui to sign session cookies. **Any container restart that loses this file invalidates every logged-in browser session.** The existing `scripts/smoke.sh:119-135` already asserts this byte-stable across restart. hermes-station must keep this invariant.

---

## 4. File formats

### 4.1 `$HERMES_HOME/.env` — dotenv

Mode: `0600`. One `KEY=VALUE` per line. Sorted alphabetically by key (as written by control plane). Values may be quoted; reader strips surrounding `'` or `"`.

Example (after /admin save with anthropic + telegram):
```
ANTHROPIC_API_KEY=sk-ant-…
TELEGRAM_ALLOWED_USERS=99999999
TELEGRAM_BOT_TOKEN=12345:…
```

**Reader behavior (`config.py:123-133`):** skip blank lines, skip lines starting with `#`, skip lines without `=`, strip surrounding quotes from value.

**Writer behavior (`config.py:136-151`):** load current values, apply updates (key with value `None` → delete), write whole file back atomically.

### 4.2 `$HERMES_HOME/config.yaml`

Mode: `0600`. YAML. Single top-level key `model` with provider/default/(optionally) base_url.

After `/admin` provider save (anthropic example):
```yaml
model:
  provider: anthropic
  default: claude-sonnet-4.6
```

With a custom OpenAI-compatible provider, adds `base_url`:
```yaml
model:
  provider: custom
  default: gpt-4o-mini
  base_url: https://example.openai-compat/v1
```

Future provider extensions (e.g. multi-model fallback) may add keys under `model:`; hermes-station should preserve unknown keys round-trip rather than dropping them.

### 4.3 `$HERMES_HOME/pairing/*.json`

Mode: `0600`. JSON object. Empty `{}` when fresh.

Files:
- `telegram-approved.json` — keyed by Telegram user ID (string), value is approval metadata
- `telegram-pending.json` — same shape, awaiting approval
- `_rate_limits.json` — per-key counters for pairing rate limiting

Schemas are owned by hermes-agent — hermes-station should not invent fields, only read/copy/revoke.

---

## 5. Admin API contract

These routes are exposed at `/admin/api/*` and called by the admin UI. Their request/response shapes must remain stable so that existing browser sessions (and the smoke test) keep working across the rebuild.

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin` | Admin UI |
| GET, POST | `/admin/login` | Login page + form post |
| POST | `/admin/logout` | Logout |
| GET | `/admin/api/status` | Aggregate status (see §5.1) |
| POST | `/admin/api/gateway/{start\|stop\|restart}` | Gateway control |
| POST | `/admin/api/webui/{start\|stop\|restart}` | WebUI control |
| POST | `/admin/api/provider/setup` | Save provider + model + API key (JSON body) |
| GET | `/admin/api/channels` | Get channel form values |
| POST | `/admin/api/channels/save` | Save channel env vars (JSON body) |
| GET | `/admin/api/pairing/pending` | Pending Telegram pairings |
| GET | `/admin/api/pairing/approved` | Approved users |
| POST | `/admin/api/pairing/{approve\|deny\|revoke}` | Pairing actions |

Source of truth: `control_plane/server.py:385-403`.

### 5.1 `/admin/api/status` response shape

Stable keys that hermes-station must preserve (covered by `scripts/smoke.sh:103-111`):

```json
{
  "paths": {
    "hermes_home": "/data/.hermes",
    "config_path": "/data/.hermes/config.yaml",
    "env_path": "/data/.hermes/.env",
    "webui_state_dir": "/data/webui",
    "workspace_dir": "/data/workspace"
  },
  "model": {"provider": "...", "default": "...", "base_url": "..."},
  "channels": [{"slug": "telegram", "enabled": true, ...}, ...],
  "env_masked": {"ANTHROPIC_API_KEY": "sk-a…ke", ...},
  "provider_catalog": [...],
  "autostart": true,
  "webui": {"running": true, "healthy": true, "log_tail": [...], ...},
  "gateway": {"running": true, "healthy": true, "log_tail": [...], ...}
}
```

In hermes-station, `log_tail` should source from the real log stream (Railway logs / stdout buffer) rather than the in-memory `deque` used today, but the response shape stays the same so the admin UI keeps working unchanged during the rebuild.

---

## 6. Provider catalog

Defined in `control_plane/config.py:35-61`. Stable identifiers — `hermes-station` must keep these exact IDs so existing `config.yaml` files continue to validate.

| ID | Label | Env var | Default model | Base URL required |
|---|---|---|---|---|
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` | `anthropic/claude-sonnet-4.6` | no |
| `anthropic` | Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4.6` | no |
| `openai` | OpenAI | `OPENAI_API_KEY` | `gpt-4o` | no (default URL applied) |
| `copilot` | GitHub Copilot | `COPILOT_GITHUB_TOKEN` | `gpt-4.1` | no |
| `custom` | Custom OpenAI-compatible | `OPENAI_API_KEY` | `gpt-4o-mini` | **yes** (e.g. Ollama Cloud) |

---

## 7. Channel catalog

Defined in `control_plane/config.py:69-100`. Stable slugs.

| Slug | Primary env var | Secondary env var |
|---|---|---|
| `telegram` | `TELEGRAM_BOT_TOKEN` | `TELEGRAM_ALLOWED_USERS` |
| `discord` | `DISCORD_BOT_TOKEN` | `DISCORD_ALLOWED_USERS` |
| `slack` | `SLACK_BOT_TOKEN` | `SLACK_APP_TOKEN` |
| `whatsapp` | `WHATSAPP_ENABLED` | _(none)_ |
| `email` | `EMAIL_ADDRESS` | `EMAIL_PASSWORD` |

Adding a channel slug is a contract extension, not a break — old deploys see "unknown channel" gracefully. Removing or renaming a slug **is** a break.

---

## 8. Gateway autostart logic

Controlled by `HERMES_GATEWAY_AUTOSTART`:

- `auto` (default): start gateway when **both** a valid provider AND at least one configured channel are present.
- `1`/`true`/`on`: force autostart whenever config is sufficient.
- `0`/`false`/`off`: never autostart; admin must press Start.

Source: `should_autostart_gateway()` in `control_plane/config.py`. hermes-station may refactor the implementation but must preserve these three modes and the "auto" semantics, because users have set `HERMES_GATEWAY_AUTOSTART` values in Railway that we don't want to break.

---

## 9. Deliberately NOT part of the contract

Things hermes-station may change freely:

- The internal WebUI port `8788` — eliminated entirely (in-process mount).
- The proxy at `/` → `127.0.0.1:8788` — eliminated.
- `WebUIManager` / `GatewayManager` subprocess shape — replaced with in-process asyncio tasks.
- In-memory log `deque(maxlen=N)` — replaced with stdout streaming.
- `scripts/patch-vendor-models.py` runtime patch — replaced with proper configuration injection.
- `cp -rn` skill seeding mechanism — same behavior, different source path (pip package data).
- Status cache (`_status_cache`, `STATUS_CACHE_TTL`) — likely eliminated (single-user product, no concurrency concern).
- Container base image — may switch from `bookworm-slim` to `alpine` or distroless.
- Inclusion of `nodejs`, `npm`, `gh` in the image — may be lazy-installed into `/data` on first need.

---

## 10. Migration test plan

The CI compatibility test (`tests/test_compat.py`) is structured as:

1. Boot hermes-station container against `tests/fixtures/data-fresh/` (an empty `/data` skeleton) — verifies the fresh-boot seeding contract.
2. Boot against `tests/fixtures/data-realistic/` (a sanitized snapshot of a real long-running deployment — populated by the maintainer, gitignored by default) — verifies the contract against real-world state.
3. Assert in each case:
   - `GET /health` returns 200 within 30s
   - `GET /admin/api/status` (after login) returns paths matching `/data/.hermes/config.yaml`, etc.
   - `status.model.provider` matches the fixture's `config.yaml`
   - `/data/webui/.signing_key` is byte-identical before and after the container boot
4. POST `/admin/api/provider/setup` with a new provider, restart container, assert it persisted to `config.yaml` in the same format.

If all four pass for both fixtures (when realistic is present), the new image is a verified drop-in replacement.

---

## Appendix A — Observed environment (probe run 2026-05-11)

Built with `container build` on Apple `container` 0.12.3 against a Hermes deployment image (commit `8daca1254`).

Container env at runtime:
```
CONTROL_PLANE_INTERNAL_WEBUI_HOST=127.0.0.1
CONTROL_PLANE_INTERNAL_WEBUI_PORT=8788
HERMES_ADMIN_PASSWORD=***
HERMES_CONFIG_PATH=/data/.hermes/config.yaml
HERMES_GATEWAY_AUTOSTART=auto
HERMES_HOME=/data/.hermes
HERMES_WEBUI_AGENT_DIR=/app/vendor/hermes-agent
HERMES_WEBUI_PASSWORD=***
HERMES_WEBUI_STATE_DIR=/data/webui
HERMES_WORKSPACE_DIR=/data/workspace
HOME=/data
PORT=18999
```

`.signing_key` size on first boot: 32 bytes, mode 0600.
`state.db` initial size: 102400 bytes (100 KB) — SQLite file with empty schema applied.

---

## Known limitations

- **Opaque hermes-agent state.** The internal formats for `state.db`, `memories/`, and `bin/` are owned entirely by hermes-agent. hermes-station does not read or modify them — it preserves them verbatim across restarts. Schema migrations (if any) are hermes-agent's responsibility.
- **Pairing directory path.** `$HERMES_HOME/pairing/` is the path tracked against the currently-pinned hermes-agent version. The compat test (`tests/test_compat.py`) catches any upstream path change before it reaches a release.
- **WebUI session blobs.** `webui/sessions/` is treated as opaque by hermes-station; it is preserved across restarts but not inspected or modified.
