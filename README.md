# hermes-station

> Single-container deployment for [Hermes Agent](https://github.com/NousResearch/hermes-agent) + [Hermes WebUI](https://github.com/nesquena/hermes-webui).
> Drop-in replacement for `hermes-all-in-one` on Railway, with the upstreams pinned and an in-process control plane.

## What this is

A single Railway-deployable container that runs:

- `/` — the Hermes WebUI
- `/admin` — control plane: browser-based provider/channel setup, gateway controls, logs
- `/health` — healthcheck

Everything writes to `/data` (single Railway volume) and shares one Hermes identity across WebUI, Telegram, Discord, Slack, and other channels. See `docs/CONTRACT.md` §3 for the full filesystem layout.

## Drop-in compatibility

`hermes-station` is engineered to accept an existing `/data` volume from `hermes-all-in-one` unchanged. The CI compat test (`tests/test_compat.py`) boots the container against a fixture `/data` snapshot and asserts the contract holds. If that test is green for a given upstream version combo, the image is a verified drop-in.

## Upstream tracking

Both upstreams move fast (hermes-agent: weekly, hermes-webui: several releases/day). We pin exact versions and let Renovate open weekly batched bump PRs; CI runs the compat test; auto-merge on green.

- `hermes-agent` — pinned in `pyproject.toml` via `git+https://...@<tag>`. Tracked by Renovate's PEP 621 manager.
- `hermes-webui` — pinned in `Dockerfile` via `ARG HERMES_WEBUI_VERSION`. Tracked by Renovate's regex manager (`renovate.json5`).

See `renovate.json5` for the schedule and `.github/workflows/ci.yml` for the gate.

## Local development

```bash
# Build
container build --tag hermes-station:local .

# Run with a fresh /data
mkdir -p /tmp/hermes-station-data
container run --rm -d --name hermes-station -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=dev -e HERMES_ADMIN_PASSWORD=dev \
  --mount type=bind,source=/tmp/hermes-station-data,target=/data \
  hermes-station:local

# Smoke
curl http://127.0.0.1:8787/health
```

Apple `container` and `docker` are both supported (commands are compatible enough for the build/run flow used here).

## License

MIT — see `LICENSE`. The pinned upstreams (`hermes-agent`, `hermes-webui`) are also MIT.
