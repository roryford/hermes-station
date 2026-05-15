#!/usr/bin/env bash
# Re-run when admin UI changes. Requires container running on :8788
# (run `dx-verify.sh` first).
set -euo pipefail

# Authenticate to grab a cookie
COOKIE=$(curl -sS -c - -d "password=verify" http://127.0.0.1:8788/admin/login \
         | awk '/admin_session/ {print $6"="$7}')
[ -z "$COOKIE" ] && { echo "auth failed"; exit 1; }

mkdir -p docs/screenshots
uvx --from playwright playwright install chromium >/dev/null
HERMES_ADMIN_COOKIE="$COOKIE" uvx --from playwright python scripts/_screenshot.py \
  http://127.0.0.1:8788/admin           docs/screenshots/admin-dashboard.png \
  http://127.0.0.1:8788/admin/settings  docs/screenshots/admin-settings.png

# Reject blank/error pages [QA-driven]
for f in docs/screenshots/*.png; do
  size=$(wc -c < "$f")
  [ "$size" -gt 10000 ] || { echo "$f is suspiciously small ($size bytes)"; exit 1; }
done

echo "screenshots refreshed"
