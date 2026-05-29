# Release runbook

The full path from "merge feature PR" to "verified live on `chat.roryford.com`".

## Why this is intentionally manual

Production deploys are run by hand so the maintainer experiences the same upgrade
UX a downstream self-hoster does. Don't add CI-driven auto-deploy to
`release.yml` — it would hide usability issues that real self-hosters would hit.

## Pre-release

1. **Merge the feature PR(s) you want in the release.** All testing happens
   on `feat/*` branches via CI; main is always releasable.
2. **Pull main** and verify CI green: `git checkout main && git pull`.
3. **Decide level**: `patch` (bug fix), `minor` (feature), `major` (breaking).

## Cut the release

```bash
scripts/release.sh minor    # or patch / major
```

This script:
- Derives the current version from the latest semver git tag
- Computes the new version
- Tags `vX.Y.Z` on the current HEAD and pushes the tag

The tag push triggers `.github/workflows/release.yml`, which:
- Creates the GitHub release with auto-generated notes
- Builds and publishes `ghcr.io/roryford/hermes-station:vX.Y.Z` and `:latest`

Watch the workflow:

```bash
gh run watch
```

## Deploy to production

> **Important.** `release.yml` builds and publishes the image but does NOT
> trigger a Railway deploy. Prod must be rolled forward manually.

### The gotcha that bites every time

`railway redeploy` defaults to re-running the **same image digest** that's
already deployed. Without `--from-source`, the command is a silent no-op:
no new deployment ID, no error, and the Railway dashboard often shows the
attempt as "Deploy failed (1m)" even though nothing was actually attempted.

The dashboard's "Redeploy" button has the same default behavior. Click the
dropdown next to it and pick the option that says *"Redeploy with latest
image"* (or similar). The plain "Redeploy" alone is the no-op.

### CLI flow

From a Railway-authenticated machine:

```bash
# Make sure you're targeting prod
railway link             # or `railway status` to verify
railway status | grep -E "Environment|Linked service"

# Pull the new :latest digest and roll
railway redeploy --from-source --yes
```

Watch the rollout:

```bash
# Status text — should show "Deploying" → "Online" within ~60s
watch -n 2 'railway status | grep status:'
```

### Verify

```bash
# Compare the image revision the running container was built from against the tag's SHA
curl -sf https://chat.roryford.com/health | jq -r '.versions.image_revision'
git rev-parse vX.Y.Z   # should match
```

If the two SHAs match, prod is on the new release.

## Rollback

If something is wrong on prod after the redeploy:

1. **Image rollback** — point the service at the previous tag explicitly in
   the Railway dashboard (Source → Image → `ghcr.io/roryford/hermes-station:vX.Y.(Z-1)`).
   Save and redeploy. `:latest` will still point at the broken release until
   the next image push.
2. **Last resort** — `gh release delete vX.Y.Z` and force-rebuild `:latest`
   from the previous tag's SHA. Only needed if `:latest` itself is poisoned
   and the auto-deploy would otherwise re-roll the bad release.

## Reference: state inspection

```bash
# What image digest is currently serving prod (Railway side)
railway status --json \
  | jq -r '.environments.edges[].node.serviceInstances.edges[].node.activeDeployments[].meta.imageDigest'

# What revision the container thinks it is
curl -sf https://chat.roryford.com/health | jq '.versions'

# What's pushed to GHCR
gh api -H "Accept: application/vnd.github+json" \
  /users/roryford/packages/container/hermes-station/versions \
  | jq -r '.[].metadata.container.tags | join(",")' | head -10
```
