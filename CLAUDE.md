# hermes-station — Claude Code guide

## What this repo is

hermes-station is a container that packages hermes-webui, hermes-agent,
hindsight, and system tools (chromium, ffmpeg, tesseract, node, MCP servers)
into a single deployable unit for Railway. There is no Python application
in this repo — just a Dockerfile, an entrypoint script, supervisord config,
and a Railway template.

## Running tests

There is no fast host-side unit test run. All meaningful tests require a
running container. The minimum feedback loop is a container build.

### Build both images

Use the Apple `container` CLI (not Docker) for local runs.

> **container CLI 0.12.3 workaround**: two bugs require a staging build context.
> Bug 1: Dockerfiles > ~15KB crash buildkit before a build starts.
> Bug 2: Only root-level files are served to buildkit (subdirectory COPY silently empties).
> The build uses tar archives as root-level ADD sources to bypass bug 2,
> and a `/tmp/hs-ctx` staging dir (< 15K files) to bypass the file-count crash.

The heavy system layer (chromium/ffmpeg/tesseract/node + pinned binaries) lives
in a base image (`ghcr.io/roryford/hermes-station-base`, built by
`.github/workflows/base-image.yml`). Published multi-arch — no local base build
needed. To change base deps: edit `Dockerfile.base`, run the base-image workflow
with a bumped tag, then update the `BASE_IMAGE` arg in `Dockerfile`.

```bash
# Prepare staging dir (first time or after clean)
mkdir -p /tmp/hs-ctx
cp scripts/patch_plugin_manifests.py scripts/hermes-entrypoint.sh /tmp/hs-ctx/
cp supervisord.conf /tmp/hs-ctx/
cp .dockerignore /tmp/hs-ctx/

# (Re-)pack source tars — run this before every build when source changes
COPYFILE_DISABLE=1 tar -c --exclude '__pycache__' --exclude '*.pyc' -f /tmp/hs-ctx/tests.tar tests
COPYFILE_DISABLE=1 tar -c -f /tmp/hs-ctx/docs.tar docs

# Build
container build -f Dockerfile -t hermes-station:local /tmp/hs-ctx
container build --target test -f Dockerfile -t hermes-station:test /tmp/hs-ctx
```

### Boot the runtime container

```bash
container run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  hermes-station:local
```

Poll until healthy: `curl -s http://127.0.0.1:8787/health`

### Run host-runnable e2e tests

```bash
HERMES_STATION_E2E_URL=http://127.0.0.1:8787 \
HERMES_STATION_E2E_PASSWORD=test-admin-pw \
uv run --with pytest --with httpx \
  pytest tests/ \
    --ignore=tests/fixtures \
    --ignore=tests/test_container_toolbelt.py \
    --ignore=tests/test_plugin_manifests.py \
    -v --no-cov
```

### Run in-container tests (toolbelt + plugin manifests)

Note: `host.containers.internal` does NOT resolve in Apple container CLI.
Use `192.168.64.1` to reach the host from inside a container.

```bash
container run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest \
    tests/test_container_toolbelt.py \
    tests/test_plugin_manifests.py \
    -v --no-cov
```

### Hindsight sidecar tests

```bash
# Boot with sidecar + exposed port.
# HINDSIGHT_API_HOST=0.0.0.0 lets the test container reach it via host IP.
container run -d --name hs-test -p 8787:8787 -p 8888:8888 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  -e HINDSIGHT_SIDECAR=1 \
  -e HINDSIGHT_API_HOST=0.0.0.0 \
  hermes-station:local

container run --rm \
  -e HERMES_STATION_HINDSIGHT_SIDECAR=1 \
  -e HERMES_STATION_HINDSIGHT_SIDECAR_URL=http://192.168.64.1:8888 \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest tests/test_hindsight_sidecar.py -v --no-cov
```

### Cleanup

```bash
container stop hs-test && container rm hs-test
```

### Expected results

Full run (all tests, booted container): all pass, 0 failed, 0 skipped.

## Key environment variables

| Variable | Purpose |
|---|---|
| `HERMES_WEBUI_PASSWORD` | webui auth password (required) |
| `OPENROUTER_API_KEY` | LLM provider key |
| `HERMES_GATEWAY_ENABLED` | `1` to start the messaging platform gateway |
| `HINDSIGHT_SIDECAR` | `1` to start the hindsight memory sidecar |
| `HERMES_PATCH_AGENT_VERSION` | Hot-patch hermes-agent to a specific PyPI version at startup |
| `HERMES_PATCH_WEBUI_VERSION` | Hot-patch hermes-webui to a specific git tag at startup |

## Bumping upstream versions

- **hermes-webui**: update `HERMES_WEBUI_VERSION` and `HERMES_WEBUI_SHA` in `Dockerfile` (Renovate tracks this)
- **hermes-agent**: update `HERMES_AGENT_VERSION` in `Dockerfile` (Renovate tracks this)
- **Base image**: edit `Dockerfile.base`, run the `base-image.yml` workflow with a bumped tag, update `BASE_IMAGE` in `Dockerfile`
