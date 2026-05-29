#!/usr/bin/env bash
set -euo pipefail

LEVEL="${1:-}"
if [[ "$LEVEL" != "patch" && "$LEVEL" != "minor" && "$LEVEL" != "major" ]]; then
  echo "usage: scripts/release.sh patch|minor|major" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is dirty — commit or stash changes first" >&2
  exit 1
fi

# Derive current version from the latest semver git tag.
CURRENT="$(git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
if [[ -z "$CURRENT" ]]; then
  echo "ERROR: no semver tag found (expected vX.Y.Z)" >&2
  exit 1
fi

VERSION="$(python3 - "$LEVEL" "$CURRENT" <<'PYEOF'
import sys
level, current = sys.argv[1], sys.argv[2].lstrip("v")
major, minor, patch = map(int, current.split("."))
if level == "major":   major, minor, patch = major + 1, 0, 0
elif level == "minor": major, minor, patch = major, minor + 1, 0
else:                  major, minor, patch = major, minor, patch + 1
print(f"v{major}.{minor}.{patch}")
PYEOF
)"

echo "Tagging ${CURRENT} → ${VERSION} on $(git rev-parse --short HEAD)"
git tag "$VERSION"
git push origin "$VERSION"

cat <<EOF

Released ${VERSION} — CI will publish the GitHub release shortly.

Next: deploy to production (manual, intentionally — see docs/release-runbook.md):

  railway redeploy --from-source --yes

The --from-source flag is load-bearing. Plain 'railway redeploy' (or the
dashboard 'Redeploy' button without the 'latest image' option) re-runs
the SAME image digest and is a silent no-op.

Verify:

  curl -sf https://chat.roryford.com/health | jq -r .versions.image_revision
  git rev-parse ${VERSION}      # should match
EOF
