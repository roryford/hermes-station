# Contributing to hermes-station

## Development setup

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

This installs the control-plane dependencies and dev tools (pytest, ruff). It does **not** install hermes-agent — see below.

### The `[hermes]` extra

The `[hermes]` extra pulls in hermes-agent from GitHub:

```bash
uv pip install -e ".[dev,hermes]"
```

The container always installs it. Local unit tests do not require it — the test suite mocks what it needs. Only install this extra if you're working on gateway integration or need the full in-process agent available locally. It's a git install and takes ~30–60 seconds.

### Full container build

```bash
container build --tag hermes-station:local .

mkdir -p /tmp/hs-data
container run --rm -p 8787:8787 \
  -e HERMES_ADMIN_PASSWORD=dev -e HERMES_WEBUI_PASSWORD=dev \
  --mount type=bind,source=/tmp/hs-data,target=/data \
  hermes-station:local
```

Apple `container` and Docker are both supported.

## Running tests

```bash
pytest -q
```

This runs the unit and compat test suite. No running container needed. Coverage is enforced at 85% — the run fails if it drops below that.

**Compat test** (`tests/test_compat.py`): boots a real container against fixture data. Auto-skips if Docker/container isn't available in the environment.

**Login smoke test** (`tests/test_login_smoke.py`): requires a running container. Skipped by default unless you set `HERMES_STATION_E2E_URL`:

```bash
HERMES_STATION_E2E_URL=http://127.0.0.1:8787 HERMES_STATION_E2E_PASSWORD=dev pytest tests/test_login_smoke.py -q
```

## Compat fixtures

- `tests/fixtures/data-fresh/` — generated programmatically by conftest; no manual step needed.
- `tests/fixtures/data-realistic/` — gitignored. See [`tests/fixtures/README.md`](tests/fixtures/README.md) for how to populate it from a real Railway volume for realistic compat testing.

## Linting and formatting

```bash
ruff check hermes_station tests   # lint
ruff format hermes_station tests  # format
```

Both run in CI. PRs must be green on both.

## Upstream pinning

hermes-agent and hermes-webui are pinned to exact versions in `pyproject.toml` and `Dockerfile` respectively, and bumped weekly by Renovate. PRs that bump upstream versions run the full compat test and auto-merge on green.

## PR checklist

- [ ] `pytest -q` passes locally
- [ ] `ruff check` and `ruff format --check` pass
- [ ] If you changed the admin API contract, update `docs/CONTRACT.md`
- [ ] If you changed env vars, update `docs/configuration.md`
