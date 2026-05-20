# Getting Started

A guide for new contributors: from a fresh clone to running the full test suite.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — install once with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Container runtime** — Apple `container` CLI (macOS) or Docker

## Clone and install

```bash
git clone https://github.com/roryford/hermes-station.git
cd hermes-station
uv sync
```

`uv sync` installs all runtime and dev dependencies (pytest, ruff, mypy) into a local `.venv`. No `pip install` or `venv` commands needed.

## Unit tests (no container required)

```bash
uv run pytest tests/ --ignore=tests/fixtures --ignore=tests/test_compat_realistic.py -q
```

All tests are hermetic — no ambient services or ports required. Expected: ~820 passed.

Or use the Makefile shortcut:

```bash
make test
```

## Full e2e suite (requires a container)

The full suite adds toolbelt checks and e2e tests that need a running container.

### 1. Build both images

| Apple container | Docker |
|---|---|
| `container build -t hermes-station:local .` | `docker build -t hermes-station:local .` |
| `container build --target test -t hermes-station:test .` | `docker build --target test -t hermes-station:test .` |

Or: `make build` (uses whichever runtime is on your PATH).

### 2. Boot the runtime container

**Apple container:**
```bash
container run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e HERMES_ADMIN_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  hermes-station:local
```

**Docker:**
```bash
docker run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e HERMES_ADMIN_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  hermes-station:local
```

Poll until healthy: `curl -s http://127.0.0.1:8787/health`

### 3. Run host-runnable tests (unit + e2e + login smoke)

```bash
HERMES_STATION_E2E_URL=http://127.0.0.1:8787 \
HERMES_STATION_E2E_PASSWORD=test-admin-pw \
HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \
uv run pytest tests/ \
  --ignore=tests/fixtures \
  --ignore=tests/test_compat_realistic.py \
  --ignore=tests/test_container_toolbelt.py \
  --ignore=tests/test_plugin_manifests.py \
  -v --no-cov
```

### 4. Run in-container tests (toolbelt + plugin manifests)

> **Apple container note:** `host.containers.internal` does not resolve. Use `192.168.64.1` to reach the host from inside a container.

**Apple container:**
```bash
container run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  -e HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov
```

**Docker:**
```bash
docker run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://host.docker.internal:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  -e HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \
  --add-host=host.docker.internal:host-gateway \
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov
```

### 5. Cleanup

```bash
container stop hs-test && container rm hs-test   # Apple container
docker stop hs-test && docker rm hs-test          # Docker
```

## One-shot full verification

`scripts/dx-verify.sh` runs all tiers in sequence (lint, unit, build, health checks, in-container suite):

```bash
bash scripts/dx-verify.sh
# or
make verify
```

## What's next

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — PR workflow, linting, compat fixtures
- **[CLAUDE.md](CLAUDE.md)** — full test matrix including the Playwright browser suite, expected pass counts, and the permanently-skipped test
- **[docs/configuration.md](docs/configuration.md)** — env var reference and first-boot behavior
