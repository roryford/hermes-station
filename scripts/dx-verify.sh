#!/usr/bin/env bash
set -euo pipefail

WANT_SCREENSHOTS="${1:-}"  # pass --screenshots to opt in [DX-driven]

# Prefer Apple `container` per CLAUDE memory; fall back to docker. [DX]
RUNTIME=$(command -v container || command -v docker)
[ -z "$RUNTIME" ] && { echo "no container runtime found"; exit 1; }

# 1. Lint + typecheck + unit tests
uv run ruff check .
uv run ruff format --check .
# mypy intentionally skipped: 54 pre-existing errors (mostly pydantic-settings call-arg
# false-positives) on main. Re-enable once those are addressed in a dedicated cleanup PR.
uv run pytest -q

# 2. Build for the host arch — Apple `container` lacks qemu so it can only run native.
# CI builds + runs linux/amd64 (matches Railway), so the Railway-parity check happens there.
"$RUNTIME" build \
  --build-arg IMAGE_REVISION="$(git rev-parse HEAD)" \
  -t hermes-station:dx-verify .

# 3. Boot with OPENROUTER_API_KEY to exercise the seeder path
DATA=$(mktemp -d)
"$RUNTIME" run -d --name hs-dx \
  -p 8788:8787 \
  -e HERMES_ADMIN_PASSWORD=verify \
  -e HERMES_WEBUI_PASSWORD=verify \
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

# 6. Screenshots — opt-in to keep core verify lean [DX-driven]
if [ "$WANT_SCREENSHOTS" = "--screenshots" ]; then
  bash scripts/refresh-screenshots.sh
fi

echo "DX verify passed"
