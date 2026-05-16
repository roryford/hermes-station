#!/usr/bin/env bash
# Generate release notes for a tag.
#
# Output is a three-part document:
#
#   1. Categorized PR list (from GitHub's PR-aware generator, configured via
#      .github/release.yml). Empty when no PRs landed in the range.
#   2. "Other changes" fallback: any commits in the range that aren't part
#      of a merged PR — covers direct-to-main changes (ci, chore, deps)
#      that GitHub's generator silently drops.
#   3. Highlights: the full body of any merged PR in the range that carries
#      the `release-highlight` label. Lets landmark features ship with the
#      "why" + test plan from the PR description instead of just a title.
#
# Why this exists as a script (not inline workflow YAML):
# - Identical logic runs in CI on tag push AND locally for backfilling
#   notes onto already-published releases (via `gh release edit`).
# - The original inline bash grew past the point where YAML escaping was
#   hiding bugs.
#
# Usage:
#   scripts/generate-release-notes.sh <tag>
#
# Requires: gh, jq, git. Authenticated via $GITHUB_TOKEN (set by Actions or
# `gh auth login` locally).

set -euo pipefail

TAG="${1:-${GITHUB_REF_NAME:-}}"
if [[ -z "$TAG" ]]; then
  echo "usage: $0 <tag>" >&2
  exit 2
fi

REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"

# Previous semver tag — the most recent v* tag reachable from TAG's first
# parent. Using `git describe ${TAG}^` (not just `tag --sort` + head) so we
# get the tag that the COMMIT graph says came before, not just the highest
# tag in lex order. Matters when backfilling notes for an older tag: for
# v0.2.0, the graph-correct PREV is v0.1.4 even though v0.2.1 sorts higher.
PREV=$(git describe --tags --abbrev=0 --match='v*' "${TAG}^" 2>/dev/null || true)
if [[ -z "$PREV" ]]; then
  echo "ERROR: no previous v* tag found before $TAG" >&2
  exit 1
fi
echo "::notice::previous tag: $PREV" >&2

# ---------------------------------------------------------------------------
# Part 1 — GitHub's categorized PR-based notes
# ---------------------------------------------------------------------------
# generate-notes returns markdown that includes a "## Category" heading per
# label-defined bucket plus a "**Full Changelog**" footer. We drop the footer
# (we append our own at the end) and trim trailing whitespace.
RAW_NOTES=$(gh api \
  -X POST "repos/${REPO}/releases/generate-notes" \
  -f tag_name="${TAG}" \
  -f previous_tag_name="${PREV}" \
  --jq .body)

# Strip the auto-appended changelog link and the HTML comment header so we
# control the final layout.
CATEGORIZED=$(echo "$RAW_NOTES" \
  | sed '/^<!-- Release notes generated/d' \
  | sed '/^\*\*Full Changelog\*\*/d' \
  | awk 'NF {p=1} p' \
  | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')

# ---------------------------------------------------------------------------
# Part 2 — commits not attributed to any merged PR
# ---------------------------------------------------------------------------
# Collect every commit subject in the range, then drop any subject that ends
# with "(#NNN)" — those are PR squash-merges already covered by Part 1. The
# rest are direct-to-main commits (CI fixes, lockfile syncs, version bumps).
ALL_COMMITS=$(git log "${PREV}..${TAG}" --pretty=format:"- %s" --no-merges || true)
NON_PR_COMMITS=$(echo "$ALL_COMMITS" \
  | grep -vE '\(#[0-9]+\)$' \
  | grep -vE '^- chore: bump (to|version to) v?[0-9]' \
  || true)

OTHER_SECTION=""
if [[ -n "$NON_PR_COMMITS" ]]; then
  OTHER_SECTION=$'\n\n## 🔩 Other changes\n\n'"$NON_PR_COMMITS"
fi

# ---------------------------------------------------------------------------
# Part 3 — highlights from `release-highlight`-labeled PRs in the range
# ---------------------------------------------------------------------------
# Extract every PR number that appears in Part 1's categorized output, then
# filter to those carrying the release-highlight label. Avoids the date-based
# heuristic that the previous version used (which got the boundary wrong when
# a tag was cut minutes after a PR merged).
# GitHub's generator renders PRs as full URLs (.../pull/NNN), not "#NNN" —
# match both forms so this works whether the API style changes or someone
# hand-edits the notes.
PR_NUMBERS=$(echo "$CATEGORIZED" \
  | grep -oE '(/pull/|#)[0-9]+' \
  | sed -E 's@(/pull/|#)@@' \
  | sort -u || true)
HIGHLIGHTS=""
for n in $PR_NUMBERS; do
  meta=$(gh pr view "$n" --json title,body,labels 2>/dev/null || echo '{}')
  has_label=$(echo "$meta" | jq -r '.labels[]?.name' | grep -c '^release-highlight$' || echo 0)
  if [[ "$has_label" -gt 0 ]]; then
    title=$(echo "$meta" | jq -r '.title')
    body=$(echo "$meta" | jq -r '.body')
    HIGHLIGHTS+=$'\n### #'"$n"' — '"$title"$'\n\n'"$body"$'\n'
  fi
done

HIGHLIGHTS_SECTION=""
if [[ -n "$HIGHLIGHTS" ]]; then
  HIGHLIGHTS_SECTION=$'\n\n## 🌟 Highlights\n'"$HIGHLIGHTS"
fi

# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------
FOOTER="**Full Changelog:** https://github.com/${REPO}/compare/${PREV}...${TAG}"

# If Part 1 came back empty (no PRs at all in the range), still produce
# a meaningful header so the section doesn't render as just whitespace.
if [[ -z "$CATEGORIZED" ]]; then
  CATEGORIZED="_No pull requests merged in this range._"
fi

cat <<EOF
${CATEGORIZED}${OTHER_SECTION}${HIGHLIGHTS_SECTION}

${FOOTER}
EOF
