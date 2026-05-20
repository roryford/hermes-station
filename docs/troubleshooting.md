# Troubleshooting local development

Common failure modes when running hermes-station locally or under CI.

---

## Container won't start

**Symptom:** `container run` / `docker run` exits immediately or `curl http://127.0.0.1:8787/health` never returns 200.

**Likely cause:** Missing required env vars, port conflict, or an image build error.

**Fix:**

1. Check the container logs for the error message:

   ```bash
   # Apple container
   container logs hs-test

   # Docker
   docker logs hs-test
   ```

2. Confirm both required env vars are set (`HERMES_WEBUI_PASSWORD` and `HERMES_ADMIN_PASSWORD`). The container will not start without these two passwords.

3. Check for a port conflict. If something else is already on `8787`:

   ```bash
   lsof -i :8787
   ```

   Change the host port mapping (e.g. `-p 8788:8787`) and update `HERMES_STATION_E2E_URL` to match.

4. Verify the image built successfully:

   ```bash
   # Apple container
   container images | grep hermes-station

   # Docker
   docker images | grep hermes-station
   ```

---

## E2E tests time out on the readiness probe

**Symptom:** Tests fail with a connection error or timeout against `http://127.0.0.1:8787`.

**Likely cause:** The container is not running, is still booting, or `HERMES_STATION_E2E_URL` points to the wrong address.

**Fix:**

1. Confirm the container is running:

   ```bash
   # Apple container
   container list

   # Docker
   docker ps
   ```

2. Poll the health endpoint manually before running tests:

   ```bash
   curl -s http://127.0.0.1:8787/health | jq .status
   ```

   Expected: `"ok"`. If nothing responds, the container is not booted or the port mapping is wrong.

3. Make sure `HERMES_STATION_E2E_URL` matches the port you mapped. If you used `-p 8788:8787`, set:

   ```bash
   HERMES_STATION_E2E_URL=http://127.0.0.1:8788
   ```

---

## `host.containers.internal` doesn't resolve (Apple container CLI)

**Symptom:** In-container tests fail to reach the host with a DNS resolution error for `host.containers.internal`.

**Likely cause:** The Apple `container` CLI does not inject `host.containers.internal` into container DNS, unlike Docker Desktop.

**Fix:** Use `192.168.64.1` instead. This is the host address reachable from inside Apple `container` CLI containers when the host is listening on `0.0.0.0`:

```bash
container run --rm \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  ...
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov
```

---

## Realistic fixture import fails

**Symptom:** `tests/test_compat_realistic.py` fails with an import or assertion error, or runs unexpectedly when it should skip.

**Likely cause (runs unexpectedly):** `tests/fixtures/data-realistic/.hermes/` exists but was populated manually with an incorrect layout. The test activates as soon as that directory exists.

**Likely cause (assertion error in `test_sanitized_env_loads_cleanly`):** The fixture was not sanitized correctly — `.env` values were not replaced with `PLACEHOLDER_<KEY>`.

**Fix:** Re-generate the fixture using the sanitize script:

```bash
./scripts/sanitize-data-snapshot.sh <path-to-snapshot.tgz>
```

This script scrubs all secrets and deletes PII directories automatically. See [`docs/fixtures.md`](fixtures.md) for the full workflow including how to take the initial snapshot.

After re-running the script, verify the fixture passes:

```bash
uv run pytest tests/test_compat_realistic.py -v
```

---

## `uv pip install` fails with EUCLEAN

**Symptom:** `uv pip install` inside a Dockerfile fails with an error like `EUCLEAN: filesystem state is unexpected (os error 117)` or similar hardlink errors.

**Likely cause:** Apple `container` CLI uses VirtioFS to share the filesystem between the host and the container. VirtioFS does not support hardlinks, and `uv` defaults to hardlink mode when installing packages.

**Fix:** Add `--link-mode=copy` to every `uv pip install` call in the Dockerfile:

```dockerfile
RUN uv pip install --link-mode=copy -e ".[dev]"
```

This is already applied in the project's `Dockerfile`. If you see this error in a custom build stage or a local venv inside a container, add the flag explicitly.

---

## Playwright tests auto-skip

**Symptom:** All `tests/browser/` tests are collected but immediately skipped with a message about `HERMES_STATION_E2E_URL` not being set.

**Likely cause:** This is expected behavior. The Playwright browser suite skips automatically when `HERMES_STATION_E2E_URL` is unset so it does not interfere with the default `uv run pytest` run.

**Fix:** If you intend to run the browser suite, boot a container with the pilot admin extension enabled and set the env var:

```bash
# Boot container with the pilot flag
container run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e HERMES_ADMIN_PASSWORD=test-admin-pw \
  -e OPENROUTER_API_KEY=local-fake-key \
  -e HERMES_STATION_PILOT_ADMIN_EXTENSION=1 \
  hermes-station:local

# Install Chromium once (cached at $PLAYWRIGHT_BROWSERS_PATH)
PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright \
  uv run --with playwright python -m playwright install chromium

# Stage 1: parallel-safe read-only tests
PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright \
HERMES_STATION_E2E_URL=http://127.0.0.1:8787 \
HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  uv run --with playwright --with pytest-playwright --with pytest-xdist \
    pytest tests/browser/ -m "not serial" --no-cov -n auto

# Stage 2: serial mutation tests (must run alone)
PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright \
HERMES_STATION_E2E_URL=http://127.0.0.1:8787 \
HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  uv run --with playwright --with pytest-playwright --with pytest-xdist \
    pytest tests/browser/ -m serial --no-cov
```

See [`CLAUDE.md`](../CLAUDE.md) for the full two-stage browser suite invocation.
