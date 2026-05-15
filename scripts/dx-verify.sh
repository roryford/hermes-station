#!/usr/bin/env bash
set -euo pipefail

WANT_SCREENSHOTS="${1:-}"  # pass --screenshots to opt in [DX-driven]

# Preflight: require uv and a container runtime.
command -v uv >/dev/null || { echo "uv not found — install from https://docs.astral.sh/uv/"; exit 1; }

# Prefer Apple `container` per CLAUDE memory; fall back to docker. [DX]
RUNTIME=$(command -v container || command -v docker)
[ -z "$RUNTIME" ] && { echo "no container runtime found (install Docker or Apple container)"; exit 1; }

# Ensure dev deps are installed before running anything.
uv sync --quiet

# 1. Lint + typecheck + unit tests
uv run ruff check .
uv run ruff format --check .
uv run mypy hermes_station --ignore-missing-imports
uv run pytest -q

# 2. Build for the host arch — Apple `container` lacks qemu so it can only run native.
# CI builds + runs linux/amd64 (matches Railway), so the Railway-parity check happens there.
# Build both stages; `test` extends `runtime` so layers are shared.
"$RUNTIME" build \
  --target runtime \
  --build-arg IMAGE_REVISION="$(git rev-parse HEAD)" \
  -t hermes-station:dx-verify .
"$RUNTIME" build \
  --target test \
  --build-arg IMAGE_REVISION="$(git rev-parse HEAD)" \
  -t hermes-station:dx-test .

# 3. Boot with OPENROUTER_API_KEY to exercise the seeder path
# Password matches HERMES_STATION_E2E_ADMIN_PASSWORD / HERMES_STATION_E2E_PASSWORD defaults
# in test_e2e_admin.py + test_login_smoke.py so no extra env vars are needed for those tests.
E2E_PW=test-admin-pw
DATA=$(mktemp -d)
"$RUNTIME" run -d --name hs-dx \
  -p 8788:8787 \
  -e HERMES_ADMIN_PASSWORD="$E2E_PW" \
  -e HERMES_WEBUI_PASSWORD="$E2E_PW" \
  -e OPENROUTER_API_KEY=sk-or-v1-VERIFY \
  -v "$DATA:/data" \
  hermes-station:dx-verify

trap '"$RUNTIME" rm -f hs-dx >/dev/null 2>&1; rm -rf "$DATA"' EXIT

# 4. Wait for /health (90s budget, > Railway's 60s) [Ops-driven]
for i in $(seq 1 90); do
  curl -sf http://127.0.0.1:8788/health > /tmp/hs-health.json && break
  sleep 1
done

# 5. Assert all DX fixes are live
jq -e '.versions.hermes_webui != null and .versions.hermes_webui != ""'  /tmp/hs-health.json
jq -e '.versions.image_revision != null and .versions.image_revision != ""' /tmp/hs-health.json
jq -e '.readiness."provider:openrouter".intended == true'  /tmp/hs-health.json
jq -e '.readiness."provider:openrouter".ready == true'     /tmp/hs-health.json
jq -e '.status == "ok"'                                    /tmp/hs-health.json
# Gateway must autostart when provider is configured (no channel required)
jq -e '.components.gateway.state == "running"'             /tmp/hs-health.json

# 6. Container-toolbelt tests — run inside the test image where the binaries exist
"$RUNTIME" run --rm hermes-station:dx-test \
  python -m pytest tests/test_container_toolbelt.py -q --no-cov

# 7. E2e + smoke — point at the already-running container
HERMES_STATION_E2E_URL=http://127.0.0.1:8788 \
  HERMES_STATION_E2E_PASSWORD="$E2E_PW" \
  HERMES_STATION_E2E_ADMIN_PASSWORD="$E2E_PW" \
  uv run pytest tests/test_e2e_admin.py tests/test_login_smoke.py tests/test_e2e_dx.py -q --no-cov

# 8. Screenshots — opt-in to keep core verify lean [DX-driven]
if [ "$WANT_SCREENSHOTS" = "--screenshots" ]; then
  bash scripts/refresh-screenshots.sh
fi

echo "DX verify passed"
