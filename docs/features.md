# Features

hermes-station bundles a broad set of capabilities behind one container. Most of them activate the moment you supply the relevant key — no rebuild, no restart. This page is the catalogue: what's supported, what each option costs/requires, and how the agent picks between alternatives when you set more than one.

Anything listed here can be set, overridden, or disabled at runtime from `/admin/settings` → **Secrets**. The same keys also work as Railway / Docker env vars at boot. Once a key is present, hermes-station forwards it to both the in-process agent and the WebUI subprocess automatically — no `config.yaml` edit required.

To verify a capability end-to-end after setting its key, open `/admin/smoketest` — the smoketest runs an HTTP probe against each configured backend and reports pass/fail with the exact error.

> **Source of truth:** this page is regenerated against the in-code catalogues. If you find a key here that doesn't work, or a key the admin UI shows that's missing here, that's a bug — file an issue. The canonical lists live in `hermes_station/admin/secrets_catalog.py`, `hermes_station/admin/provider.py`, and `hermes_station/admin/channels.py`.

## LLM providers

The agent needs exactly one primary LLM provider. On first boot, if any of the auto-seed keys are set, hermes-station writes a matching `model:` block to `config.yaml` — precedence is table order, first non-empty wins.

| Provider | Key | Default model | Auto-seed | Notes |
| --- | --- | --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | `anthropic/claude-sonnet-4.6` | ✓ (1st) | One key, hundreds of models. Recommended starting point. |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4.6` | ✓ (2nd) | Direct Anthropic billing. |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` | ✓ (3rd) | Direct OpenAI billing. |
| GitHub Copilot | `COPILOT_GITHUB_TOKEN` | `gpt-4.1` | — | Also accepts `GH_TOKEN` / `GITHUB_TOKEN`. OAuth flow available in `/admin`. |
| xAI (SuperGrok) | `XAI_API_KEY` | `grok-4` | — | Or use the Connect with xAI button for SuperGrok / X Premium+ OAuth — no key needed. |
| Custom OpenAI-compatible | `OPENAI_API_KEY` + `base_url` | `gpt-4o-mini` | — | Point at any OpenAI-API-shaped endpoint (Together, Groq's OpenAI API, local llama.cpp, etc.). |

After the agent boots, switch providers from `/admin/settings` → **Provider**. The auto-seed only runs once on a fresh `/data`.

## Channels

Each channel is independent — enable any combination. All channels share one Hermes identity, so a conversation moves cleanly between them.

| Channel | Required key | Optional key | Setup |
| --- | --- | --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN` | `TELEGRAM_ALLOWED_USERS` (restrict by user ID) | `@BotFather` → `/newbot` → paste token |
| Discord | `DISCORD_BOT_TOKEN` | `DISCORD_ALLOWED_USERS` | Discord Developer Portal → enable Message Content Intent → OAuth invite |
| Slack | `SLACK_BOT_TOKEN` (xoxb-…) | `SLACK_APP_TOKEN` (xapp-…) for Socket Mode | api.slack.com/apps → bot scopes `app_mentions:read`, `chat:write` |
| Email | `EMAIL_ADDRESS` + `EMAIL_PASSWORD` | `EMAIL_DISPLAY_NAME` | IMAP/SMTP via himalaya. App password (not account password). Gmail / iCloud / generic auto-detected by domain. |
| WhatsApp | `WHATSAPP_ENABLED=1` | — | Requires the Hermes WhatsApp bridge running separately. |

Step-by-step instructions for each channel render inside `/admin/settings` → **Channels**.

## Web search

If `web.search_backend` is set in `config.yaml`, the agent uses that single backend. The default install does not enable any backend — set one to activate web search.

| Backend | Key | Cost |
| --- | --- | --- |
| DuckDuckGo (`ddgs`) | _no key required_ | Free |
| Brave Search (`brave`) | `BRAVE_API_KEY` | Paid tier |
| Brave Free (`brave-free`) | `BRAVE_SEARCH_API_KEY` | Free tier |
| Tavily (`tavily`) | `TAVILY_API_KEY` | 1000 free / month |
| SerpAPI (`serpapi`) | `SERPAPI_API_KEY` | Paid (Google/Bing/DDG scrape) |
| Google Custom Search (`google`) | `GOOGLE_CSE_API_KEY` | Free quota + paid tier |
| Firecrawl (`firecrawl`) | `FIRECRAWL_API_KEY` | Web scraping with rendered-page extraction |
| Exa (`exa`) | `EXA_API_KEY` | Neural search with semantic ranking |
| Parallel (`parallel`) | `PARALLEL_API_KEY` | — |
| SearXNG (`searxng`) | `SEARXNG_URL` | Self-hosted (e.g. `http://searxng.railway.internal:8080`) |

To pick a backend, edit `config.yaml`:

```yaml
web:
  search_backend: ddgs   # or brave, tavily, exa, ...
```

Only the key matching the selected backend needs to be set. If multiple keys are set, the agent still uses only the configured `search_backend`.

## Voice (TTS / STT)

Voice providers cover text-to-speech, speech-to-text, or both. Mix-and-match — the agent picks per task.

| Provider | Key | Covers |
| --- | --- | --- |
| ElevenLabs | `ELEVENLABS_API_KEY` | TTS |
| Deepgram | `DEEPGRAM_API_KEY` | STT |
| Groq | `GROQ_API_KEY` | STT (Whisper) + fast LLM inference |
| Mistral | `MISTRAL_API_KEY` | STT (Voxtral) + Mistral LLMs |

Set one or more keys. Selection happens inside hermes-agent based on which providers are credentialed.

## Memory

The agent has long-term memory across sessions. Holographic memory is on by default and requires no external service. The hosted alternatives provide cross-session user modelling and richer retrieval.

| Provider | Key | Notes |
| --- | --- | --- |
| Holographic | _no key required_ | Local, default. Stored under `/data/.hermes/memory/`. |
| Mem0 | `MEM0_API_KEY` | Hosted long-term memory provider. |
| Honcho | `HONCHO_API_KEY` | Cross-session user modelling. |
| Supermemory | `SUPERMEMORY_API_KEY` | Hosted long-term memory backend. |

To switch provider, edit `config.yaml`:

```yaml
memory:
  provider: holographic   # or mem0, honcho, supermemory
```

## Image generation

| Backend | Key | Notes |
| --- | --- | --- |
| FAL.ai | `FAL_KEY` | flux, gpt-image, nano-banana, etc. |
| xAI / Grok | `XAI_API_KEY` | xAI image generation (also enables Grok text models — see LLM providers). |

## Browser automation

When the agent needs to drive a real browser (rendered pages, login flows, scraping JS-heavy sites), it uses one of these backends. Set the key for the one you want — the agent prefers self-hosted (`CAMOFOX_URL`) over cloud when both are set.

| Backend | Key | Notes |
| --- | --- | --- |
| Camofox (self-hosted) | `CAMOFOX_URL` | URL of your Camofox service. Takes priority when set. |
| Browser Use (cloud) | `BROWSER_USE_API_KEY` | Free tier. Auto-detected by hermes-agent. |
| Browserbase | `BROWSERBASE_API_KEY` + `BROWSERBASE_PROJECT_ID` | Managed headless browser sessions. |
| Steel | `STEEL_API_KEY` | 100 hrs/month free. Requires hermes-agent ≥ next release. |

## Observability

Optional. When all three Langfuse keys are set, hermes-agent emits traces.

| Key | Purpose |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY` | Public key. |
| `LANGFUSE_SECRET_KEY` | Secret key. |
| `LANGFUSE_HOST` | Base URL (e.g. `https://cloud.langfuse.com` or self-hosted). |

## MCP tools

hermes-station seeds a curated MCP server set in `config.yaml` on first boot, all `enabled: false` so you opt-in per server from `/admin/settings` → **MCP servers**.

| Server | Backed by | Activation requirement |
| --- | --- | --- |
| `filesystem` | `mcp-server-filesystem` scoped to `/data/workspace` | Toggle on. |
| `fetch` | `mcp-server-fetch` (HTTP → Markdown) | Toggle on. |
| `github` | `mcp-server-github` | Toggle on + `GITHUB_TOKEN` (or `GH_TOKEN`). |
| `playwright-mcp` | Remote Railway service | Toggle on + reachable URL. |

Custom MCP servers can be added via the admin UI or `config.yaml`. See [`secrets.md`](./secrets.md) for the in-process vs. sandboxed-tool credential model.

## Verifying a capability

After setting any of the keys above, two surfaces tell you whether it's working:

- **`/health`** — the `readiness` map carries one row per intended capability. `ready: true` means the startup probe passed; `ready: false` carries a human-readable `reason`. The container does not crash on missing keys (warn-and-continue model).
- **`/admin/smoketest`** — runs a live HTTP probe against each configured backend (LLM provider, GitHub MCP, web search, image gen, browser, plugin registry). Reports pass/fail with the exact error response. Use this after credential changes to confirm the agent can actually reach the service.

## Disabling and overriding

Every key on this page also accepts the three-state model from `/admin/settings` → **Secrets**:

- **Auto** — value comes from the process env (Railway, Docker `-e`, etc.).
- **Override** — value stored in `/data/.hermes/.env`, takes precedence over the env.
- **Disabled** — added to `admin.disabled_secrets` in `config.yaml`. hermes-station pops the key from `os.environ` at boot so the agent cannot see it — useful for selectively hiding a Railway-injected secret without removing it from the dashboard.

See [`secrets.md`](./secrets.md) for the full state model, shadow detection, and sandboxed-tool passthrough.
