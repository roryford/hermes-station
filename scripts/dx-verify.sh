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

# 3. Boot the TEST stage image as a server — this single container runs the
# full test suite (unit + toolbelt + e2e) in step 7 via `exec`.
# Expose a port so the host can run /health assertions in step 5.
E2E_PW=test-admin-pw
DATA=$(mktemp -d)
"$RUNTIME" run -d --name hs-dx \
  -p 8788:8787 \
  -e HERMES_ADMIN_PASSWORD="$E2E_PW" \
  -e HERMES_WEBUI_PASSWORD="$E2E_PW" \
  -e OPENROUTER_API_KEY=sk-or-v1-VERIFY \
  -v "$DATA:/data" \
  hermes-station:dx-test \
  python -m hermes_station

trap '"$RUNTIME" rm -f hs-dx >/dev/null 2>&1; rm -rf "$DATA"' EXIT

# 4. Wait for /health (90s budget, > Railway's 60s) [Ops-driven]
for i in $(seq 1 90); do
  curl -sf http://127.0.0.1:8788/health > /tmp/hs-health.json && break
  sleep 1
done

# 5. Assert all DX fixes are live (from host, same as an operator would check)
jq -e '.versions.hermes_webui != null and .versions.hermes_webui != ""'  /tmp/hs-health.json
jq -e '.versions.image_revision != null and .versions.image_revision != ""' /tmp/hs-health.json
jq -e '.readiness."provider:openrouter".intended == true'  /tmp/hs-health.json
jq -e '.readiness."provider:openrouter".ready == true'     /tmp/hs-health.json
jq -e '.status == "ok"'                                    /tmp/hs-health.json
# Gateway must autostart when provider is configured (no channel required)
jq -e '.components.gateway.state != "unknown"'             /tmp/hs-health.json

# 6. Run the complete test suite inside the container — unit + toolbelt + e2e.
# HERMES_STATION_E2E_URL points at localhost so the e2e tests (which normally
# skip when the URL is unset) exercise the live server in the same container.
"$RUNTIME" exec \
  -e HERMES_STATION_E2E_URL=http://localhost:8787 \
  -e HERMES_STATION_E2E_ADMIN_PASSWORD="$E2E_PW" \
  -e HERMES_STATION_E2E_PASSWORD="$E2E_PW" \
  hs-dx \
  python -m pytest tests/ -q --no-cov

# 8. Screenshots — opt-in to keep core verify lean [DX-driven]
if [ "$WANT_SCREENSHOTS" = "--screenshots" ]; then
  bash scripts/refresh-screenshots.sh
fi

echo "DX verify passed"
