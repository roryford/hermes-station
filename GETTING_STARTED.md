# Getting Started

A guide for new contributors: from a fresh clone to running the full test suite.

## Prerequisites

- **Container runtime** — Apple `container` CLI (macOS) or Docker
- **`uv`** (optional) — for running host-side tests: `curl -LsSf https://astral.sh/uv/install.sh | sh`

There is no Python application to set up locally. All meaningful testing requires a container build.

## Clone

```bash
git clone https://github.com/roryford/hermes-station.git
cd hermes-station
```

## Build the container image

See [`CLAUDE.md`](CLAUDE.md) for the complete build workflow, including the Apple `container` CLI staging-dir workaround. Quick summary:

**Apple container (requires staging dir):**

```bash
# Prepare staging dir
mkdir -p /tmp/hs-ctx
cp scripts/patch_plugin_manifests.py scripts/hermes-entrypoint.sh /tmp/hs-ctx/
cp supervisord.conf .dockerignore /tmp/hs-ctx/
COPYFILE_DISABLE=1 tar -c --exclude '__pycache__' --exclude '*.pyc' -f /tmp/hs-ctx/tests.tar tests
COPYFILE_DISABLE=1 tar -c -f /tmp/hs-ctx/docs.tar docs

container build -f Dockerfile -t hermes-station:local /tmp/hs-ctx
container build --target test -f Dockerfile -t hermes-station:test /tmp/hs-ctx
```

**Docker (direct build):**

```bash
docker build -t hermes-station:local .
docker build --target test -t hermes-station:test .
```

## Boot the runtime container

**Apple container:**
```bash
container run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  hermes-station:local
```

**Docker:**
```bash
docker run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  hermes-station:local
```

Poll until healthy:

```bash
curl -s http://127.0.0.1:8787/health | jq .status
```

Expected: `"ok"`.

## Run host-runnable e2e tests

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

## Run in-container tests

> **Apple container note:** `host.containers.internal` does not resolve. Use `192.168.64.1` to reach the host from inside a container.

**Apple container:**
```bash
container run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov
```

**Docker:**
```bash
docker run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://host.docker.internal:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov
```

## Cleanup

```bash
container stop hs-test && container rm hs-test   # Apple container
docker stop hs-test && docker rm hs-test          # Docker
```

## What's next

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — PR workflow and checklist
- **[CLAUDE.md](CLAUDE.md)** — full build and test matrix including the Hindsight sidecar tests
- **[docs/configuration.md](docs/configuration.md)** — env var reference and first-boot behavior
- **[docs/architecture.md](docs/architecture.md)** — container structure and process model
