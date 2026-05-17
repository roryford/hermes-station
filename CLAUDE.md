# hermes-station — Claude Code guide

## Running tests

Always run the **full** test suite — no skipping, no `-k` filters, no `--ignore` (except the two noted below).

### Quick unit + lint run (no container needed)

```bash
uv run pytest tests/ --ignore=tests/fixtures --ignore=tests/test_compat_realistic.py -q
```

### Full test suite (including e2e and toolbelt — requires a running container)

Use the Apple `container` CLI (not Docker) for local runs.

**1. Build both images:**
```bash
container build -t hermes-station:local .
container build --target test -t hermes-station:test .
```

**2. Boot the runtime container:**
```bash
container run -d --name hs-test -p 8787:8787 \
  -e HERMES_WEBUI_PASSWORD=test-admin-pw \
  -e HERMES_ADMIN_PASSWORD=test-admin-pw \
  hermes-station:local
```
Poll until healthy: `curl -s http://127.0.0.1:8787/health`

**3. Run host-runnable tests (unit + e2e + login smoke):**
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

**4. Run in-container tests (toolbelt + plugin manifests) from inside the test image:**

Note: `host.containers.internal` does NOT resolve in Apple container CLI. Use `192.168.64.1` to reach the host from inside a container.

```bash
container run --rm \
  -e HERMES_STATION_REQUIRE_TOOLBELT=1 \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  -e HERMES_STATION_E2E_PASSWORD=test-admin-pw \
  -e HERMES_STATION_E2E_ADMIN_PASSWORD=test-admin-pw \
  hermes-station:test \
  python -m pytest \
    tests/test_container_toolbelt.py \
    tests/test_plugin_manifests.py \
    -v --no-cov
```

**5. Cleanup:**
```bash
container stop hs-test && container rm hs-test
```

### Permanently skipped

- `tests/test_compat_realistic.py` — requires `tests/fixtures/data-realistic/.hermes/` which is not committed to the repo.

### Expected results

Full run: ~820 passed, 0 failed, 0 skipped (excluding `test_compat_realistic.py`).
