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

1. Export a backup via the **Backup** card in the Station panel at
   `/` → Settings → Admin (see [Backup and restore](#2-backup-and-restore)).
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
3. Open `/admin` and confirm the status indicators are green. Run the smoke
   tests from the **Smoke tests** card if available.
4. Check `/health` for `status: "ok"`. `status: "degraded"` means a
   configured capability is missing its secret — inspect the `readiness` map
   for which row has `ready: false`.

### Rollback

1. **Soft rollback** — flip any pilot flags off via Railway env vars, then
   restart the service. The previous image keeps serving until the new one
   passes the healthcheck.
2. **Image rollback** — in the Railway dashboard set the source image back to
   the previous version tag (e.g. `ghcr.io/roryford/hermes-station:vX.Y.Z`),
   save, and redeploy. `:latest` remains on the broken release until the next
   push.
3. **Last resort** — see [`docs/release-runbook.md`](release-runbook.md#rollback)
   for forcing `:latest` back to a known-good SHA.

Rolling back the image never rolls back `/data`. The volume format is forward-
compatible across minor versions. If a release contains a data migration that
cannot be reversed, that will be noted in the release notes.

---

## 2. Backup and restore

### What is captured

The backup archive (`hermes-station-backup-<timestamp>.tar.gz`) contains the
following files from `$HERMES_HOME` (`/data/.hermes/`):

| Included | Notes |
|---|---|
| `config.yaml` | Provider, model, channel, MCP, and feature configuration |
| `state.db` | SQLite agent state — conversation history, task state |
| `gateway_state.json` | Last-known gateway lifecycle state |
| `SOUL.md` | Agent personality file (if present) |
| `memories/` | Long-term holographic memory (if present) |
| `pairing/` | Channel pairing state — Telegram, Discord tokens etc. (if present) |

**Not captured:**

- `/data/.hermes/.env` — secrets are excluded by design. Manage keys via the
  Secrets page or Railway env vars and re-enter them after restore.
- `/data/webui/` — the webui signing key and chat session blobs.
- `/data/workspace/` — user-managed workspace files.
- Any runtime state files (`gateway.lock`, `gateway.pid`, etc.) — regenerated
  at boot.

### Taking a backup

The Backup card is available in the **Station panel** in the webui settings
(`/` → Settings → Admin, requires `HERMES_STATION_PILOT_ADMIN_EXTENSION=1`).

The backup endpoint stops the gateway, flushes the SQLite WAL checkpoint so
the archive gets a clean `state.db`, then restarts the gateway. The whole
sequence is synchronous from the browser's perspective — the download starts
when the archive is ready.

You can also call the API directly:

```bash
curl -sf -X POST https://your-app/admin/api/pilot/backup/download \
  -b "hermes_station_admin=<your-session-cookie>" \
  -o hermes-station-backup.tar.gz
```

### Restoring to an existing deployment

Use the Restore button in the same Backup card. The endpoint:

1. Stops the gateway.
2. Validates the uploaded archive against the allowlist (same set of files
   listed above — no arbitrary paths accepted).
3. Extracts the archive into `$HERMES_HOME`, overwriting existing files.
4. Restarts the gateway.

After a restore you must re-enter secrets (provider API key, channel tokens)
via the Secrets page at `/admin/settings` because `.env` is not included in the
archive.

### Restoring to a fresh container

1. Deploy a fresh hermes-station service with a new volume. Wait for it to
   reach `status: "ok"` at `/health` (first-boot state, no secrets yet).
2. Open the Station panel and use the Restore button to upload your archive.
3. Restart the container (Railway dashboard → "Restart service" or a redeploy).
4. Re-enter secrets via `/admin/settings` → Secrets.
5. Verify `/health` returns `status: "ok"` and all intended capabilities show
   `ready: true`.

---

## 3. Migrating to a new Railway project

Use this when moving between Railway accounts, regions, or starting fresh with
a new template deploy.

### Overview

The migration transfers: the Railway volume contents, all env vars, and
optionally the custom domain.

### Steps

1. **Take a backup** (see section 2) from the source deployment.

2. **Provision the destination** — deploy a fresh hermes-station template in
   the new project. Wait for first-boot health to pass.

3. **Copy env vars** — open the source service's Variables tab in the Railway
   dashboard and note all custom vars (`HERMES_WEBUI_PASSWORD`,
   `HERMES_ADMIN_PASSWORD`, any `*_API_KEY` vars you manage via Railway rather
   than the Secrets page). Set the same vars on the destination service.

4. **Restore the backup** — use the Station panel Restore button on the
   destination to upload the archive from step 1.

5. **Restart the destination** container.

6. **Verify** — open `/health` and confirm `status: "ok"`. Re-enter any
   secrets that were stored in `.env` (not in Railway vars) via
   `/admin/settings` → Secrets. Test core flows: chat, channel connectivity.

7. **DNS cutover** — update your custom domain's CNAME to point at the
   destination Railway service's generated domain. Railway's certificate
   provisioning is automatic; allow a few minutes for propagation.

8. **Decommission the source** — once the destination is verified, remove the
   source service (and optionally the volume) from the old project.

### Volume-level copy (alternative for large datasets)

For large `state.db` files the in-app backup may be slow. As an alternative,
use `railway run` to get a shell on the source service and stream the relevant
files directly:

```bash
# On the source service, from a railway run session:
tar -czf - -C /data .hermes/state.db .hermes/config.yaml .hermes/memories \
    .hermes/pairing .hermes/SOUL.md > snapshot.tar.gz
```

Then restore manually by extracting into `/data/.hermes/` on the destination
volume and restarting.

---

## 4. Provider key rotation

hermes-station supports low-disruption key rotation for all provider and service
keys. The `.env` file takes precedence over Railway env vars. Saving a new value
via the Secrets page rewrites `.env` atomically and triggers an automatic gateway
restart so the new key takes effect — no container redeploy required.

### Rotate a key via the admin UI

1. Open `/admin/settings` → Secrets.
2. Find the key row (e.g. `OPENROUTER_API_KEY`).
3. Click **Save override** and enter the new key value. The old value is
   overwritten atomically in `/data/.hermes/.env`.
4. The Secrets page triggers a gateway restart automatically after saving a
   provider key. Verify the **Source** badge changes to `file` and the gateway
   comes back up in the status indicators.

### Rotate a Railway-managed key (no `.env` override)

If you manage the key directly in Railway's Variables tab (source badge shows
`env`):

1. Update the value in the Railway dashboard.
2. Restart the service (a redeploy is not required — a plain restart picks up
   the new env).
3. Verify via `/health` that the relevant readiness row shows `ready: true`.

### Handle the shadowing warning

If a key exists in both Railway and `.env`, the Secrets page shows a
"Railway also sets …" warning. After rotating the Railway-side key, click
**Use Railway** to drop the `.env` override so the rotation takes effect.

### Verify after rotation

```bash
curl -sf https://your-app/health | jq '.readiness'
```

The relevant `provider:*` or capability row should show `"ready": true`.
If the gateway fails to reconnect, inspect live logs via `/admin/logs` or
Railway's log stream for authentication errors from the provider.

---

## 5. Recovering from a bad config

Use this when the container is running but misconfigured, or when `/admin`
is unreachable.

### Admin UI is reachable

1. Open `/admin/settings` to fix provider or channel config.
2. Use the Secrets page to correct any key values.
3. Use the gateway controls to stop and restart the gateway.
4. If hermes-webui is in a crash loop, restart it via the **Restart WebUI**
   button on the admin dashboard.

### Admin UI is unreachable (control plane is down)

If the control plane itself is crashing (Railway shows repeated restarts), the
most common cause is a corrupt `config.yaml`. Reset it:

1. Use `railway run` (or Railway's shell access if available) to get a shell on
   the running volume and rename the bad file:

   ```bash
   mv /data/.hermes/config.yaml /data/.hermes/config.yaml.bak
   ```

2. Restart the service. The boot-time seeders will write a fresh `config.yaml`
   from env var defaults (provider auto-seed, memory defaults, etc.).
3. Re-enter any config that was in the renamed file.

### Reset secrets (.env)

If `.env` contains a bad value that prevents startup:

```bash
# Via railway run shell:
mv /data/.hermes/.env /data/.hermes/.env.bak
```

After restart the process reads keys from Railway env vars only. Re-enter any
needed overrides via the Secrets page.

### Nuclear reset (start over, keep volume)

To return the volume to a clean first-boot state while keeping the volume
itself (avoiding Railway volume re-creation charges):

```bash
# Via railway run shell — deletes all agent state including conversation history:
rm -rf /data/.hermes /data/webui /data/workspace
```

Restart. The container boots as if the volume were new. You will lose all
conversation history, memory, and pairing state.

### `/health` stays `down`

`status: "down"` means `/data` is not writable. This is a volume mount
failure, not a config error. Check:

- Railway volume is attached to the service (Volumes tab in the dashboard).
- The volume is not full (check Railway's metrics for disk usage).
- The `hermes` user (uid 10000) has write access — the entrypoint runs
  `chown -R 10000 /data` at startup; if the container crashed before that
  completed, a restart usually resolves it.
