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

python3 - "$LEVEL" <<'PYEOF'
import sys, re, pathlib
level = sys.argv[1]
p = pathlib.Path("pyproject.toml")
text = p.read_text()
m = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text, re.MULTILINE)
if not m:
    sys.exit("could not find version in pyproject.toml")
major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
if level == "major":   major, minor, patch = major + 1, 0, 0
elif level == "minor": major, minor, patch = major, minor + 1, 0
else:                  major, minor, patch = major, minor, patch + 1
new_ver = f"{major}.{minor}.{patch}"
p.write_text(text[:m.start()] + f'version = "{new_ver}"' + text[m.end():])
print(new_ver)
PYEOF
VERSION="v$(python3 -c "import re,pathlib; m=re.search(r'version\s*=\s*\"([^\"]+)\"', pathlib.Path('pyproject.toml').read_text()); print(m.group(1))")"

git add pyproject.toml
git commit -m "chore: bump to ${VERSION}"
git tag "$VERSION"
git push
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
