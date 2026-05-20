# Contributing to hermes-station

## Development setup

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
source .venv/bin/activate
```

This installs the control-plane dependencies and dev tools (pytest, ruff). It does **not** install hermes-agent — see below.

### The `[hermes]` extra

The `[hermes]` extra pulls in hermes-agent from GitHub:

```bash
uv pip install -e ".[dev,hermes]"
```

The container always installs it. Local unit tests do not require it — the test suite mocks what it needs. Only install this extra if you're working on gateway integration or need the full in-process agent available locally. It's a git install and takes ~30–60 seconds.

### Full container build

Both Apple `container` and Docker are supported — the commands are compatible for build and run:

```bash
# Apple container
container build --tag hermes-station:local .

# Docker
docker build --tag hermes-station:local .
```

```bash
mkdir -p /tmp/hs-data

# Apple container
container run --rm -p 8787:8787 \
  -e HERMES_ADMIN_PASSWORD=dev -e HERMES_WEBUI_PASSWORD=dev \
  --mount type=bind,source=/tmp/hs-data,target=/data \
  hermes-station:local

# Docker
docker run --rm -p 8787:8787 \
  -e HERMES_ADMIN_PASSWORD=dev -e HERMES_WEBUI_PASSWORD=dev \
  -v /tmp/hs-data:/data \
  hermes-station:local
```

## Running tests

The full test matrix is documented in [`CLAUDE.md`](CLAUDE.md). This section covers the quick summary and Docker-specific notes for contributors who don't have the Apple `container` CLI.

### Quick unit run (no container needed)

```bash
uv run pytest tests/ --ignore=tests/fixtures --ignore=tests/test_compat_realistic.py -q
```

All tests are hermetic — no ambient services, ports, or filesystem state required. Coverage is enforced at 85%.

### Full suite (container required)

Both Apple `container` and Docker are supported for the runtime container. Use whichever you have:

**Build:**

| Apple `container` | Docker |
|---|---|
| `container build -t hermes-station:local .` | `docker build -t hermes-station:local .` |
| `container build --target test -t hermes-station:test .` | `docker build --target test -t hermes-station:test .` |

**Boot the runtime container:**

```bash
# Apple container
container run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e HERMES_ADMIN_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  hermes-station:local

# Docker
docker run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e HERMES_ADMIN_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  hermes-station:local
```

Poll until healthy: `curl -s http://127.0.0.1:8787/health`

**Run host-side tests (unit + e2e + login smoke):**

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

**Run in-container tests (toolbelt + plugin manifests):**

> **Apple `container` note:** `host.containers.internal` does not resolve; use `192.168.64.1` to reach host-forwarded ports from inside a container.

```bash
# Apple container
container run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  -e HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov

# Docker (Mac/Windows — Docker Desktop)
docker run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://host.docker.internal:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  -e HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov
```

> **Docker Linux note:** `host.docker.internal` is not injected on Linux without `--add-host`. Use `--add-host=host-gateway:$(ip route | awk '/default/ {print $3}')` and replace `host.docker.internal` with `host-gateway`.

**Cleanup:**

```bash
# Apple container
container stop hs-test && container rm hs-test

# Docker
docker stop hs-test && docker rm hs-test
```

See [`CLAUDE.md`](CLAUDE.md) for the full step-by-step matrix including the Playwright browser suite. See [`docs/troubleshooting.md`](docs/troubleshooting.md) for common failure modes.

### Full DX verification (single command)

`scripts/dx-verify.sh` runs the complete local verification pipeline in one shot: lint, typecheck, unit tests, container build, health assertions, and the full in-container test suite. Use it before opening a PR to confirm nothing is broken end-to-end:

```bash
bash scripts/dx-verify.sh
```

Pass `--screenshots` to also regenerate the UI screenshots:

```bash
bash scripts/dx-verify.sh --screenshots
```

## Compat fixtures

- `tests/fixtures/data-fresh/` — generated programmatically by conftest; no manual step needed.
- `tests/fixtures/data-realistic/` — gitignored. See [`docs/fixtures.md`](docs/fixtures.md) for the full workflow to generate it from a real Railway volume.

## Linting and formatting

```bash
ruff check hermes_station tests        # lint
ruff format hermes_station tests       # format
ruff format --check hermes_station tests  # check only (what CI runs)
```

Both lint and format are enforced in CI. PRs must be green on both.

## Upstream pinning and Renovate

hermes-agent and hermes-webui are pinned to exact versions in `pyproject.toml` and `Dockerfile` respectively. A [Renovate](https://docs.renovatebot.com/) bot opens weekly PRs to bump them — Renovate requires a [GitHub App install](https://github.com/apps/renovate) to run on forks. If you fork this repo and want automated dependency updates, install the Renovate app and it will pick up `renovate.json5` automatically. Without it, you'll still get GitHub Actions updates via Dependabot (configured in `.github/dependabot.yml`), but Python and Docker dep bumps will need to be done manually.

## PR workflow

1. Fork the repo and create a branch: `git checkout -b my-fix`
2. Make your changes
3. Run `uv run pytest tests/ --ignore=tests/fixtures --ignore=tests/test_compat_realistic.py -q` and `ruff check hermes_station tests` locally (see [Running tests](#running-tests))
4. Push your branch and open a PR against `main`

## PR checklist

- [ ] `uv run pytest tests/ --ignore=tests/fixtures --ignore=tests/test_compat_realistic.py -q` passes locally
- [ ] `ruff check` and `ruff format --check` pass
- [ ] If you changed the admin API contract, update `docs/CONTRACT.md`
- [ ] If you changed env vars, update `docs/configuration.md`
