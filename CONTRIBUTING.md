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

### Unit suite (no container needed)

```bash
pytest -q
```

All tests are hermetic — no ambient services, ports, or filesystem state required. Coverage is enforced at 85%.

### E2e and smoke tests (requires a running container)

Boot a container first:

```bash
docker run -d --name hs-local -p 8788:8787 \
  -e HERMES_ADMIN_PASSWORD=test-admin-pw \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  ghcr.io/roryford/hermes-station:latest
```

Then run:

```bash
HERMES_STATION_E2E_URL=http://127.0.0.1:8788 \
  HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  pytest tests/test_e2e_admin.py tests/test_login_smoke.py -q
```

### Container toolbelt tests (run inside the image)

These verify that required binaries (`tesseract`, `fd`, `node`, etc.) are present in the built image. They must run inside the container:

```bash
docker build --target test -t hermes-station:test .
docker run --rm hermes-station:test pytest tests/test_container_toolbelt.py -q
```

### Full local verification

`scripts/dx-verify.sh` runs all three tiers in sequence: unit tests, image build, container health checks, toolbelt tests, and e2e tests.

## Compat fixtures

- `tests/fixtures/data-fresh/` — generated programmatically by conftest; no manual step needed.
- `tests/fixtures/data-realistic/` — gitignored. See [`tests/fixtures/README.md`](tests/fixtures/README.md) for how to populate it from a real Railway volume for realistic compat testing.

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
3. Run `pytest -q` and `ruff check hermes_station tests` locally (see [Running tests](#running-tests))
4. Push your branch and open a PR against `main`

## PR checklist

- [ ] `pytest -q` passes locally
- [ ] `ruff check` and `ruff format --check` pass
- [ ] If you changed the admin API contract, update `docs/CONTRACT.md`
- [ ] If you changed env vars, update `docs/configuration.md`
