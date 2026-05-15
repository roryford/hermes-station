# Deploy and Host Hermes Agent

> Template config is source-controlled at [`/railway-template.json`](../railway-template.json) in this repo — edit there and re-publish via the Railway dashboard's import flow when changing the template.

[Hermes Agent](https://github.com/NousResearch/hermes-agent) is an open-source AI assistant you run on your own infrastructure. hermes-station packages it into a single Railway-deployable container with a browser-based setup wizard — no config files to edit, no SSH required.

![Admin dashboard](screenshots/admin-dashboard.png)

![Admin settings](screenshots/admin-settings.png)

## About Hosting

hermes-station runs as a single container backed by one Railway volume at `/data`. Everything — agent state, conversation history, credentials, memory — lives on that volume. Swap the container image for a new version and your data persists untouched.

The container exposes three surfaces:

- **`/`** — Hermes web chat UI
- **`/admin`** — control plane: provider setup, channel management, gateway controls, live logs
- **`/health`** — structured JSON healthcheck used by Railway's readiness probe

On first boot with no config, the container starts in degraded mode and walks you through setup via `/admin`. Nothing crashes — it just waits for you to add credentials.

## Why Deploy

- **Privacy** — your conversations and API keys never leave your infrastructure
- **Flexibility** — swap LLM providers in seconds from the admin UI; no redeployment needed
- **Multi-channel** — one agent identity across web, Telegram, Discord, Slack, email, and WhatsApp
- **Memory** — holographic memory enabled by default; the agent remembers context across sessions
- **MCP tools** — filesystem, GitHub, and web fetch tools pre-cached in the image, toggleable from `/admin`
- **Cost visibility** — token spend shown in the UI by default so runaway loops are immediately visible

## Common Use Cases

- Personal AI assistant reachable from Telegram or Discord on your phone
- Team bot connected to a shared Slack workspace
- Self-hosted alternative to ChatGPT with your own Anthropic or OpenRouter key
- Local-model gateway using Ollama or another OpenAI-compatible endpoint
- Automated agent with scheduled tasks and long-term memory

## Dependencies for hermes-station

hermes-station has no external service dependencies beyond the LLM provider you choose. Everything runs inside the container.

| Dependency | Required | Notes |
|---|---|---|
| LLM provider API key | Yes | OpenRouter, Anthropic, OpenAI, Copilot, or custom |
| Railway volume | Yes | Mounted at `/data` — persists all agent state |
| `HERMES_ADMIN_PASSWORD` | Recommended | Locks `/admin`; open to anyone if unset |
| `HERMES_WEBUI_PASSWORD` | Recommended | Locks the web chat UI; open to anyone if unset |
| Telegram / Discord / Slack credentials | No | Only needed if you want those channels |

### Deployment Dependencies

| Service | Purpose | Required |
|---|---|---|
| Railway Volume | Persistent storage for agent state, memory, and credentials | **Yes** |
| LLM Provider | Powers the agent — OpenRouter recommended for easiest setup | **Yes** |
| Telegram Bot | Reach the agent from Telegram | No |
| Discord Bot | Reach the agent from Discord | No |
| Slack App | Reach the agent from Slack | No |

### Setup after deploy

1. Open your Railway URL (the `*.up.railway.app` address shown in the Railway dashboard) — the **FIRST RUN wizard** launches automatically on first visit and walks you through choosing a provider and model.
2. If you set `OPENROUTER_API_KEY` (or `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) during deploy, the wizard's system check will confirm it is configured — you can skip the provider step and start chatting immediately.
3. Open `/admin` to connect Telegram or Discord, toggle MCP servers, start/stop the gateway, and view live logs.

The gateway starts automatically once a provider is configured. See [`docs/configuration.md`](configuration.md#provider-auto-seed) for the full provider auto-seed precedence and default-model table.
