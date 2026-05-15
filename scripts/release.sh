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

hatch version "$LEVEL"
VERSION="v$(hatch version)"

git add pyproject.toml
git commit -m "chore: bump to ${VERSION}"
git tag "$VERSION"
git push
git push origin "$VERSION"

echo "Released ${VERSION} — CI will publish the GitHub release shortly."
