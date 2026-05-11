#!/usr/bin/env bash
# Sanitize a /data snapshot from a live hermes-all-in-one Railway deployment
# into a fixture suitable for tests/fixtures/data-realistic/.
#
# Opinionated: only copies the three directories hermes-station's CONTRACT.md
# governs (.hermes/, webui/, workspace/). Everything else in the snapshot
# (.cache/, .npm/, .local/, .config/, lost+found/, user content like wiki/ or
# hermes-patches/) is discarded — those aren't part of hermes-station's
# contract and including them just bloats the fixture and risks broken cache
# symlinks.
#
# Usage:
#   ./scripts/sanitize-data-snapshot.sh <snapshot.tgz> [dest-dir]
#
# Defaults dest-dir to tests/fixtures/data-realistic.
#
# What gets scrubbed in .hermes/:
#   - .env                              values → PLACEHOLDER_<KEY> (key-only)
#   - pairing/*.json + platforms/pairing/*.json
#                                       all values → "PLACEHOLDER"
#   - auth.json (if present)            ditto
# What gets DELETED in .hermes/ (PII or regenerable on next boot):
#   - sessions/*, memories/*, logs/*, sandboxes/*, bin/*, cache/*, cron/*
#   - state.db + state.db-shm + state.db-wal
#   - memory_store.db + memory_store.db-shm + memory_store.db-wal
#   - kanban.db
#   - gateway.lock, gateway.pid, gateway_state.json, processes.json
# What's PRESERVED in .hermes/:
#   - config.yaml (non-secret), SOUL.md (manual review prompt in MANIFEST.txt)
#   - skills/, optional-skills/ (catalog content; not PII)
#   - channel_directory.json, models_dev_cache.json (cached metadata)
#
# In webui/:
#   - .signing_key → random 32 bytes (we test it persists, not its value)
#   - sessions/* deleted (chat content is PII)
#
# In workspace/:
#   - emptied entirely (user files; the directory's existence is the contract)

set -euo pipefail

SNAPSHOT="${1:?usage: sanitize-data-snapshot.sh <snapshot.tgz> [dest-dir]}"
DEST="${2:-tests/fixtures/data-realistic}"

if [ ! -f "$SNAPSHOT" ]; then
    echo "snapshot not found: $SNAPSHOT" >&2
    exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_ABS="$(cd "$ROOT_DIR" && mkdir -p "$DEST" && cd "$DEST" && pwd)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[1/8] extracting $SNAPSHOT to staging area..."
tar -xzf "$SNAPSHOT" -C "$TMP"

# Locate the /data root in the extracted snapshot. Tarballs may be rooted at
# /data, ./data, or directly at the contents — try each.
SRC=""
for candidate in "$TMP/data" "$TMP/data/." "$TMP/.hermes/.." "$TMP/." ; do
    if [ -d "$candidate/.hermes" ]; then
        SRC="$candidate"
        break
    fi
done
if [ -z "$SRC" ]; then
    echo "could not locate /data root in snapshot (looked for a .hermes/ child)" >&2
    exit 1
fi
echo "      snapshot root: $SRC"

echo "[2/8] copying contract-relevant dirs only (.hermes/, webui/, workspace/)..."
rm -rf "$DEST_ABS"
mkdir -p "$DEST_ABS"
# Use cp -a per top-level dir; skip anything else (cache, npm, lost+found, user content)
for top in .hermes webui workspace; do
    if [ -d "$SRC/$top" ]; then
        cp -a "$SRC/$top" "$DEST_ABS/"
    else
        # Workspace might be missing on a fresh-ish deploy — create empty for the contract
        mkdir -p "$DEST_ABS/$top"
    fi
done

# Helper: replace every VALUE in a dotenv with a placeholder keyed by the KEY name.
scrub_dotenv() {
    local f="$1"
    [ -f "$f" ] || return 0
    python3 - "$f" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
out = []
for line in p.read_text(encoding="utf-8").splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        out.append(line)
        continue
    key, _ = s.split("=", 1)
    key = key.strip()
    out.append(f"{key}=PLACEHOLDER_{key}")
p.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
PY
}

# Helper: replace every value in a flat JSON object with a placeholder.
scrub_json_object() {
    local f="$1"
    [ -f "$f" ] || return 0
    python3 - "$f" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
try:
    data = json.loads(p.read_text(encoding="utf-8") or "{}")
except json.JSONDecodeError:
    data = {}
if isinstance(data, dict):
    scrubbed = {str(k): "PLACEHOLDER" for k in data.keys()}
elif isinstance(data, list):
    scrubbed = []
else:
    scrubbed = {}
p.write_text(json.dumps(scrubbed, indent=2) + "\n", encoding="utf-8")
PY
}

echo "[3/8] scrubbing .env + JSON files with secrets..."
scrub_dotenv "$DEST_ABS/.hermes/.env"
for f in "$DEST_ABS/.hermes/pairing/"*.json \
         "$DEST_ABS/.hermes/platforms/pairing/"*.json \
         "$DEST_ABS/.hermes/auth.json"; do
    [ -e "$f" ] || continue
    scrub_json_object "$f"
done

echo "[4/8] deleting PII directories (sessions, memories, logs, sandboxes)..."
for d in sessions memories logs sandboxes bin cache cron; do
    target="$DEST_ABS/.hermes/$d"
    if [ -d "$target" ]; then
        rm -rf "$target"
        mkdir -p "$target"
    fi
done
# Workspace is user content; empty entirely (keep dir for contract)
if [ -d "$DEST_ABS/workspace" ]; then
    rm -rf "$DEST_ABS/workspace"
    mkdir -p "$DEST_ABS/workspace"
fi

echo "[5/8] deleting SQLite DBs (incl. WAL/SHM sidecars) and runtime state files..."
# state.db, memory_store.db: drop with their journal sidecars.
# WAL/SHM files exist when SQLite is in WAL mode — leaving sidecars without the main file corrupts.
for prefix in state memory_store; do
    rm -f "$DEST_ABS/.hermes/${prefix}.db" \
          "$DEST_ABS/.hermes/${prefix}.db-shm" \
          "$DEST_ABS/.hermes/${prefix}.db-wal" \
          "$DEST_ABS/.hermes/${prefix}.db-journal"
done
rm -f "$DEST_ABS/.hermes/kanban.db" \
      "$DEST_ABS/.hermes/kanban.db-shm" \
      "$DEST_ABS/.hermes/kanban.db-wal" \
      "$DEST_ABS/.hermes/kanban.db-journal"
# Gateway runtime state — regenerated each boot.
rm -f "$DEST_ABS/.hermes/gateway.lock" \
      "$DEST_ABS/.hermes/gateway.pid" \
      "$DEST_ABS/.hermes/gateway_state.json" \
      "$DEST_ABS/.hermes/processes.json"
# Per-platform gateway state subdirs (regenerated)
if [ -d "$DEST_ABS/.hermes/gateway" ]; then
    rm -rf "$DEST_ABS/.hermes/gateway"
fi

echo "[6/8] replacing webui/.signing_key with random 32 bytes..."
if [ -d "$DEST_ABS/webui" ]; then
    head -c 32 /dev/urandom > "$DEST_ABS/webui/.signing_key"
    chmod 600 "$DEST_ABS/webui/.signing_key"
fi
# WebUI sessions: empty (chat blobs are PII)
if [ -d "$DEST_ABS/webui/sessions" ]; then
    rm -rf "$DEST_ABS/webui/sessions"
    mkdir -p "$DEST_ABS/webui/sessions"
fi

echo "[7/8] writing MANIFEST.txt..."
cat > "$DEST_ABS/MANIFEST.txt" <<EOF
Sanitized hermes-all-in-one /data snapshot.

Source snapshot: $(basename "$SNAPSHOT")
Sanitized at:    $(date -u +%Y-%m-%dT%H:%M:%SZ)

Top-level dirs included:  .hermes/ webui/ workspace/
Top-level dirs DROPPED:   everything else (.cache, .npm, .local, .config,
                          lost+found, user content like wiki/, hermes-patches/)

Scrubbed (keys preserved, values replaced):
  - .hermes/.env                                values → PLACEHOLDER_<KEY>
  - .hermes/pairing/*.json + platforms/pairing  values → "PLACEHOLDER"
  - .hermes/auth.json                           values → "PLACEHOLDER"

Emptied (dir kept, contents deleted):
  - .hermes/{sessions,memories,logs,sandboxes,bin,cache,cron}
  - webui/sessions
  - workspace/

Deleted entirely (regenerated by hermes-agent on next boot):
  - .hermes/state.db + WAL/SHM
  - .hermes/memory_store.db + WAL/SHM
  - .hermes/kanban.db + WAL/SHM
  - .hermes/gateway.lock, gateway.pid, gateway_state.json, processes.json
  - .hermes/gateway/ subdir
  - webui/.signing_key (replaced with random 32 bytes)

Preserved (still review manually if sensitive):
  - .hermes/config.yaml (provider, model — non-secret)
  - .hermes/SOUL.md (your agent's personality file — may want to scrub)
  - .hermes/skills/, optional-skills/ (skill catalog)
  - .hermes/channel_directory.json, models_dev_cache.json (cached metadata)
EOF

echo "[8/8] sanitized fixture size:"
du -sh "$DEST_ABS"

echo ""
echo "Done. Fixture at $DEST_ABS"
echo "Review SOUL.md and check skills/ for anything sensitive before sharing."
