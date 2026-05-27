# Contributing to hermes-station

## What this repo is

hermes-station is a container packaging layer — a Dockerfile, entrypoint script,
supervisord config, and Railway template. There is no Python application in this
repo. Contributions are typically:

- Changes to `Dockerfile` or `Dockerfile.base`
- Changes to `scripts/hermes-entrypoint.sh` or `supervisord.conf`
- Changes to `railway-template.json`
- Documentation updates
- Test additions or fixes

## Local build

There is no fast host-side run — all meaningful testing requires a container build.
See [`CLAUDE.md`](CLAUDE.md) for the full build and test workflow, including the
Apple `container` CLI staging-dir workaround.

### Container runtime

Both Apple `container` and Docker are supported:

```bash
# Apple container (see CLAUDE.md for staging dir setup)
container build -f Dockerfile -t hermes-station:local /tmp/hs-ctx

# Docker (builds directly)
docker build -t hermes-station:local .
```

### Run a local container

```bash
mkdir -p /tmp/hs-data

# Apple container
container run --rm -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=dev \
  --mount type=bind,source=/tmp/hs-data,target=/data \
  hermes-station:local

# Docker
docker run --rm -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=dev \
  -v /tmp/hs-data:/data \
  hermes-station:local
```

## Running tests

The full test matrix is documented in [`CLAUDE.md`](CLAUDE.md).

### Host-runnable e2e tests (requires a booted container)

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

### In-container tests (toolbelt + plugin manifests)

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

> **Apple container note:** `host.containers.internal` does not resolve. Use `192.168.64.1` to reach host-forwarded ports from inside a container.

For Docker, use `host.docker.internal` instead.

## Upstream pinning and Renovate

hermes-webui is pinned to an exact version in `Dockerfile` via `ARG HERMES_WEBUI_VERSION`. hermes-agent is pinned via install args in `Dockerfile`. A [Renovate](https://docs.renovatebot.com/) bot opens weekly PRs to bump them. Without it, you'll get GitHub Actions updates via Dependabot but Docker dep bumps will need to be done manually.

## PR workflow

1. Fork the repo and create a branch off main: `git checkout -b my-fix`
2. Make your changes
3. Build and smoke-test locally (see CLAUDE.md)
4. Push your branch and open a PR against `main`

## PR checklist

- [ ] Container builds successfully
- [ ] `curl http://127.0.0.1:8787/health | jq .status` returns `"ok"` on a fresh boot
- [ ] If you changed env vars, update `docs/configuration.md`
- [ ] If you changed the Dockerfile significantly, update `docs/architecture.md`
