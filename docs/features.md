# Features

hermes-station bundles a broad set of capabilities behind one container. Most of them activate the moment you supply the relevant key — no rebuild, no restart. This page is the catalogue: what's supported, what each option costs/requires, and how the agent picks between alternatives when you set more than one.

All keys can be set as Railway / Docker env vars at boot, or managed via the WebUI settings panel at runtime.

## LLM providers

The agent needs exactly one primary LLM provider. On first boot, if any of the auto-seed keys are set, hermes-station writes a matching `model:` block to `config.yaml` — precedence is table order, first non-empty wins.

| Provider | Key | Default model | Auto-seed | Notes |
| --- | --- | --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | `anthropic/claude-sonnet-4.6` | ✓ (1st) | One key, hundreds of models. Recommended starting point. |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4.6` | ✓ (2nd) | Direct Anthropic billing. |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` | ✓ (3rd) | Direct OpenAI billing. |
| GitHub Copilot | `COPILOT_GITHUB_TOKEN` | `gpt-4.1` | — | Also accepts `GH_TOKEN` / `GITHUB_TOKEN`. |
| xAI (SuperGrok) | `XAI_API_KEY` | `grok-4` | — | Or use the Connect with xAI button for SuperGrok / X Premium+ OAuth. |
| Custom OpenAI-compatible | `OPENAI_API_KEY` + `base_url` | `gpt-4o-mini` | — | Point at any OpenAI-API-shaped endpoint (Together, Groq's OpenAI API, local llama.cpp, etc.). |

After the agent boots, switch providers from the WebUI settings panel. The auto-seed only runs once on a fresh `/data`.

## Channels

Each channel is independent — enable any combination. All channels share one Hermes identity, so a conversation moves cleanly between them.

The gateway process must be running (`HERMES_GATEWAY_ENABLED=1`) to use any channel. The WebUI chat at `/` works without the gateway.

| Channel | Required key | Optional key | Setup |
| --- | --- | --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN` | `TELEGRAM_ALLOWED_USERS` (restrict by user ID) | `@BotFather` → `/newbot` → paste token |
| Discord | `DISCORD_BOT_TOKEN` | `DISCORD_ALLOWED_USERS` | Discord Developer Portal → enable Message Content Intent → OAuth invite |
| Slack | `SLACK_BOT_TOKEN` (xoxb-…) | `SLACK_APP_TOKEN` (xapp-…) for Socket Mode | api.slack.com/apps → bot scopes `app_mentions:read`, `chat:write` |
| Email | `EMAIL_ADDRESS` + `EMAIL_PASSWORD` | `EMAIL_DISPLAY_NAME` | IMAP/SMTP via himalaya. App password (not account password). Gmail / iCloud / generic auto-detected by domain. |
| WhatsApp | `WHATSAPP_ENABLED=1` | — | Requires the Hermes WhatsApp bridge running separately. |

## Scheduled jobs / cron

When `HERMES_GATEWAY_ENABLED=1`, the gateway also handles scheduled jobs and cron tasks. Job definitions are stored under `/data/.hermes/cron/`.

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
| SearXNG (`searxng`) | `SEARXNG_URL` | Self-hosted (e.g. `http://searxng.railway.internal:8080`) |

To pick a backend, edit `config.yaml`:

```yaml
web:
  search_backend: ddgs   # or brave, tavily, exa, ...
```

Only the key matching the selected backend needs to be set.

## Voice (TTS / STT)

Voice providers cover text-to-speech, speech-to-text, or both. Mix-and-match — the agent picks per task.

| Provider | Key | Covers |
| --- | --- | --- |
| ElevenLabs | `ELEVENLABS_API_KEY` | TTS |
| Deepgram | `DEEPGRAM_API_KEY` | STT |
| Groq | `GROQ_API_KEY` | STT (Whisper) + fast LLM inference |
| Mistral | `MISTRAL_API_KEY` | STT (Voxtral) + Mistral LLMs |

## Memory

The agent has long-term memory across sessions. Holographic memory is on by default and requires no external service.

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

## Hindsight memory sidecar

Hindsight is an optional in-container sidecar that runs a local memory API backed by an embedded Postgres database (pg0). It provides vector-based retrieval over conversation history without routing data to an external service.

**Enable it:**

```
HINDSIGHT_SIDECAR=1
```

Requires `OPENROUTER_API_KEY` to be set — the sidecar uses it for both LLM inference and embeddings. If the key is absent the sidecar is skipped with a warning and the container still boots normally.

**Configuration:**

| Variable | Default | Purpose |
| --- | --- | --- |
| `HINDSIGHT_SIDECAR` | _unset_ | Set to `1` or `true` to enable. |
| `HINDSIGHT_API_HOST` | `127.0.0.1` | Bind host. Set to `0.0.0.0` to expose the port externally. |
| `HINDSIGHT_API_PORT` | `8888` | TCP port for the Hindsight API. |
| `HINDSIGHT_API_LLM_PROVIDER` | `openrouter` | LLM provider used for memory inference. |
| `HINDSIGHT_API_LLM_MODEL` | `openai/gpt-4o-mini` | Model used for memory inference. |
| `HINDSIGHT_API_EMBEDDINGS_PROVIDER` | `openai` | Embeddings provider. |
| `HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | Embeddings API endpoint. |
| `HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL` | `text-embedding-3-small` | Embeddings model. |
| `HINDSIGHT_API_RERANKER_PROVIDER` | `rrf` | Reranker strategy (RRF = Reciprocal Rank Fusion). |
| `HINDSIGHT_API_DATABASE_URL` | `pg0://hindsight-hermes` | Postgres connection string. The embedded pg0 database stores its files under `/data/.pg0`. |

The LLM and embeddings API keys are always derived from `OPENROUTER_API_KEY`.

Sidecar logs are written to `/data/.hindsight/api.log`.

## Image generation

| Backend | Key | Notes |
| --- | --- | --- |
| FAL.ai | `FAL_KEY` | flux, gpt-image, nano-banana, etc. |
| xAI / Grok | `XAI_API_KEY` | xAI image generation (also enables Grok text models). |

## Browser automation

When the agent needs to drive a real browser (rendered pages, login flows, scraping JS-heavy sites), it uses one of these backends.

| Backend | Key | Notes |
| --- | --- | --- |
| Camofox (self-hosted) | `CAMOFOX_URL` | URL of your Camofox service. Takes priority when set. |
| Browser Use (cloud) | `BROWSER_USE_API_KEY` | Free tier. Auto-detected by hermes-agent. |
| Browserbase | `BROWSERBASE_API_KEY` + `BROWSERBASE_PROJECT_ID` | Managed headless browser sessions. |
| Steel | `STEEL_API_KEY` | 100 hrs/month free. |

## Observability

Optional. When all three Langfuse keys are set, hermes-agent emits traces.

| Key | Purpose |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY` | Public key. |
| `LANGFUSE_SECRET_KEY` | Secret key. |
| `LANGFUSE_HOST` | Base URL (e.g. `https://cloud.langfuse.com` or self-hosted). |

## MCP tools

hermes-station seeds a curated MCP server set in `config.yaml` on first boot, all `enabled: false` so you opt-in per server from the WebUI settings.

| Server | Backed by | Activation requirement |
| --- | --- | --- |
| `filesystem` | `mcp-server-filesystem` scoped to `/data/workspace` | Toggle on. |
| `fetch` | `mcp-server-fetch` (HTTP → Markdown) | Toggle on. |
| `github` | `mcp-server-github` | Toggle on + `GITHUB_TOKEN` (or `GH_TOKEN`). |
| `playwright-mcp` | Remote Railway service | Toggle on + reachable URL. |

Custom MCP servers can be added via `config.yaml`.

## Verifying a capability

After setting any of the keys above, `/health` tells you whether it's working:

- **`/health`** — the `readiness` map carries one row per intended capability. `ready: true` means the startup probe passed; `ready: false` carries a human-readable `reason`. The container does not crash on missing keys (warn-and-continue model).
