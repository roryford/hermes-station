# Ops runbook

Operational procedures for running hermes-station in production on Railway.

For release and deployment mechanics (cutting a tag, rolling to prod) see
[`docs/release-runbook.md`](release-runbook.md). This runbook covers day-to-day
operations once the service is live.

---

## 1. Upgrading an existing deployment

hermes-station images are published to `ghcr.io/roryford/hermes-station`. The
image source determines how Railway picks up a new version.

### How Railway picks up new images

**Template deploy (default):** Railway re-pulls the `:latest` tag when you
trigger a redeploy. The volume at `/data` is untouched.

**Custom image pinned to a tag:** update the source tag in Railway's service
settings (Source → Image → change the tag), then redeploy.

### Before upgrading

1. Take a backup of your `/data` volume (see [Backup and restore](#2-backup-and-restore)).
2. Note the current running version:
   ```bash
   curl -sf https://your-app/health | jq .versions
   ```
3. Review the GitHub release notes for any breaking changes or migration steps.

### Trigger the upgrade

**Gotcha**: `railway redeploy` without flags re-runs the same image digest —
it is a silent no-op. You need `--from-source` or the equivalent dashboard
option.

- **Dashboard**: click the dropdown arrow next to the "Redeploy" button and
  choose the option that pulls the latest image (wording varies by Railway UI
  version — it is distinct from the plain "Redeploy" option).
- **CLI**:
  ```bash
  railway link   # confirm you're targeting the right environment
  railway redeploy --from-source --yes
  ```

### After upgrading

1. Watch Railway's deploy log until the healthcheck passes.
2. Verify the new revision is live:
   ```bash
   curl -sf https://your-app/health | jq .versions
   ```
3. Check `/health` for `status: "ok"`. `status: "degraded"` means a
   configured capability is missing its secret — inspect the `readiness` map
   for which row has `ready: false`.

### Rollback

1. **Image rollback** — in the Railway dashboard set the source image back to
   the previous version tag (e.g. `ghcr.io/roryford/hermes-station:vX.Y.Z`),
   save, and redeploy. `:latest` remains on the broken release until the next
   push.
2. **Last resort** — see [`docs/release-runbook.md`](release-runbook.md#rollback)
   for forcing `:latest` back to a known-good SHA.

Rolling back the image never rolls back `/data`. The volume format is forward-
compatible across minor versions.

---

## 2. Backup and restore

### What is captured

A backup archive (`hermes-station-backup-<timestamp>.tar.gz`) of the following
files from `$HERMES_HOME` (`/data/.hermes/`):

| Included | Notes |
|---|---|
| `config.yaml` | Provider, model, channel, MCP, and feature configuration |
| `state.db` | SQLite agent state — conversation history, task state |
| `gateway_state.json` | Last-known gateway lifecycle state |
| `SOUL.md` | Agent personality file (if present) |
| `memories/` | Long-term holographic memory (if present) |
| `pairing/` | Channel pairing state (if present) |

**Not captured:**

- `/data/.hermes/.env` — secrets are excluded by design. Re-enter them after restore.
- `/data/webui/` — the webui signing key and chat session blobs.
- `/data/workspace/` — user-managed workspace files.

### Taking a backup

```bash
# Get a shell on the running container via Railway CLI
railway shell --service hermes-station

# Stream the tarball to a local file
tar -czf - -C /data .hermes/state.db .hermes/config.yaml \
    .hermes/memories .hermes/pairing .hermes/SOUL.md \
    > hermes-station-backup.tar.gz
```

Alternatively, if running locally:

```bash
# Apple container
container exec hermes-station tar -czf /tmp/data.tgz -C / data
container cp hermes-station:/tmp/data.tgz ./data.tgz

# Docker
docker exec hermes-station tar -czf /tmp/data.tgz -C / data
docker cp hermes-station:/tmp/data.tgz ./data.tgz
```

### Restoring

1. Stop the container (or just stop the gateway: set `HERMES_GATEWAY_ENABLED=0` and restart).
2. Extract the archive into `/data/.hermes/` on the volume:
   ```bash
   # Via railway shell:
   tar -xzf backup.tar.gz -C /data/.hermes/
   ```
3. Restart the container.
4. Re-enter secrets (provider API key, channel tokens) via Railway env vars or the WebUI settings panel.

### Restoring to a fresh container

1. Deploy a fresh hermes-station service with a new volume. Wait for `/health` to return 200.
2. Get a shell and restore the backup as above.
3. Restart the container.
4. Re-enter secrets.
5. Verify `/health` returns `status: "ok"`.

---

## 3. Migrating to a new Railway project

### Overview

The migration transfers the Railway volume contents, all env vars, and optionally the custom domain.

### Steps

1. **Take a backup** (see section 2) from the source deployment.

2. **Provision the destination** — deploy a fresh hermes-station template in
   the new project. Wait for first-boot health to pass.

3. **Copy env vars** — open the source service's Variables tab in the Railway
   dashboard and note all custom vars (`HERMES_WEBUI_PASSWORD`, any `*_API_KEY`
   vars you manage via Railway). Set the same vars on the destination service.

4. **Restore the backup** — get a shell on the destination and restore the archive.

5. **Restart the destination** container.

6. **Verify** — open `/health` and confirm `status: "ok"`. Test core flows: chat,
   channel connectivity.

7. **DNS cutover** — update your custom domain's CNAME to point at the
   destination Railway service's generated domain.

8. **Decommission the source** — once the destination is verified, remove the
   source service and volume from the old project.

---

## 4. Provider key rotation

### Rotate via Railway env vars

1. Update the value in the Railway dashboard Variables tab.
2. Restart the service (a redeploy is not required — a plain restart picks up
   the new env).
3. Verify via `/health` that the relevant readiness row shows `ready: true`.

### Verify after rotation

```bash
curl -sf https://your-app/health | jq '.readiness'
```

The relevant `provider:*` or capability row should show `"ready": true`.

---

## 5. Recovering from a bad config

### Container is running but misconfigured

1. Use Railway's environment variables to set or override the problematic keys.
2. Restart the service.
3. Verify `/health` shows `status: "ok"`.

### Config file is corrupt

If a corrupt `config.yaml` prevents the container from starting correctly:

1. Use `railway run` (or Railway's shell access) to get a shell on the volume:
   ```bash
   mv /data/.hermes/config.yaml /data/.hermes/config.yaml.bak
   ```
2. Restart the service. The boot-time seeders will write a fresh `config.yaml`
   from env var defaults.
3. Re-enter any config that was in the renamed file.

### Reset secrets (.env)

If `.env` contains a bad value:

```bash
# Via railway run shell:
mv /data/.hermes/.env /data/.hermes/.env.bak
```

After restart the process reads keys from Railway env vars only.

### Nuclear reset (start over, keep volume)

To return the volume to a clean first-boot state while keeping the volume itself:

```bash
# Via railway run shell — deletes all agent state including conversation history:
rm -rf /data/.hermes /data/webui /data/workspace
```

Restart. You will lose all conversation history, memory, and pairing state.

### `/health` stays `down`

`status: "down"` means `/data` is not writable. Check:

- Railway volume is attached to the service (Volumes tab in the dashboard).
- The volume is not full (check Railway's metrics for disk usage).
- The `hermes` user (uid 10000) has write access — the entrypoint runs
  `chown -R 10000 /data` at startup; a container restart usually resolves it.
