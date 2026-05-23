# hermes-station extension platform

hermes-station ships an optional admin extension that is injected into the
hermes-webui Settings panel. This document covers how the extension is
delivered, how it interacts with the DOM and API, and how features are
categorized for the long term.

---

## What is an extension

The extension is a JS/CSS pair (`extension/admin.js`, `extension/admin.css`)
served from the station container at `/extensions/admin.js` and
`/extensions/admin.css`. hermes-webui supports injecting external scripts and
stylesheets via three environment variables:

- `HERMES_WEBUI_EXTENSION_DIR` — filesystem path to serve extension files from
- `HERMES_WEBUI_EXTENSION_SCRIPT_URLS` — URL(s) for injected `<script>` tags
- `HERMES_WEBUI_EXTENSION_STYLESHEET_URLS` — URL(s) for injected `<link>` tags

When `HERMES_STATION_PILOT_ADMIN_EXTENSION=1` is set, station auto-seeds these
three variables at WebUI subprocess boot time (see `hermes_station/webui.py`,
`_PILOT_EXTENSION_DEFAULTS`). Operator-supplied values take precedence — if any
of the three keys are already present in the environment, the operator value is
used and a warning is logged.

The extension is entirely opt-in. Without the pilot flag the WebUI starts
without any station-injected JS or CSS.

---

## DOM contract

The extension runs as an IIFE in the webui browser context. It expects two
elements to already exist:

- `#settingsMenu` — the settings sidebar (`<ul>` of `<button>` items)
- `.settings-main` — the scrollable pane container

On load the extension appends a `<button data-settings-section="station">` to
`#settingsMenu` and a `<div id="settingsPaneAdmin" class="settings-pane">` to
`.settings-main`. If either anchor element is missing the IIFE returns early
with no side effects.

### `switchSettingsSection` wrap

webui's `switchSettingsSection` function has a hardcoded allowlist of its own
sections. Passing `"station"` to it falls back to `"conversation"`. The
extension wraps the global:

1. Captures the current value of `window.switchSettingsSection` as `_delegate`.
2. Installs a replacement that handles `"station"` directly and forwards all
   other calls to `_delegate`.
3. Locks the property with `Object.defineProperty(..., { writable: false })` to
   prevent webui's async settings init from clobbering the wrap.

Polling lifecycle is gated on a `_userOpenedStation` flag flipped by real click
events, not the pane's `.active` class. This avoids a race where webui's
`loadSettingsPanel` calls `switchSettingsSection('conversation')` at the end of
its async init sequence — that call clears `.active` even when the user is
actively viewing Station. See the inline comments in `extension/admin.js` for
the full rationale.

---

## API convention

All station pilot endpoints live under `/admin/api/pilot/*` (see
`CONTRACT.md` §12 for the full endpoint list).

### Auth — dual-cookie bridge

Pilot endpoints accept two authentication paths:

1. **Admin cookie** (`hermes_admin_session`) — issued by the station login page
   at `/admin/login`. Standard for operators who are logged into `/admin`.
2. **WebUI session bridge** — the extension runs inside the webui browser
   context where the station admin cookie is not present. The extension sends
   requests with `credentials: "include"`, which forwards the browser's
   `hermes_session` cookie. The bridge auth module
   (`hermes_station/admin/bridge_auth.py`) validates this cookie by making an
   internal loopback call to `http://127.0.0.1:8788/api/auth/status` and
   checking `logged_in == true`. A request is authorized if either cookie
   verifies.

The bridge depends on webui's `/api/auth/status` returning
`{"auth_enabled": bool, "logged_in": bool}`. This dependency is tracked in
`CONTRACT.md` §11.

### CSRF

State-changing pilot endpoints (POSTs) are defended by:

1. POST-only routing — no GET-triggered side effects.
2. Cookie auth with `SameSite=Lax` on both cookies.
3. Per-request `Origin`/`Referer` same-origin check when the header is present.
   Non-browser callers (curl, tests) without these headers are accepted.

See `CONTRACT.md` §12 for the full CSRF posture note.

---

## Cards

The extension organizes content into cards. Each card is built with the
`card(title)` helper, which returns a `<section class="admin-card">` with an
`<h3>` title. Shared helpers:

- `appendDl(section, pairs)` — renders a `<dl>` of label/value pairs
- `renderChannels(section, channels)` — renders channel readiness pills
- `fmtUptime(seconds)` — formats seconds into `Xd Yh Zm`

The current cards and their data sources:

| Card | Data source | Notes |
|---|---|---|
| Gateway | `/admin/api/pilot/status` | Polled; includes restart action |
| Provider | `/admin/api/pilot/status` | Polled |
| Channels | `/admin/api/pilot/status` | Polled |
| Memory | `/admin/api/pilot/status` | Polled |
| Versions | `/admin/api/pilot/status` | Polled |
| Usage | `/admin/api/pilot/usage` | On-demand; 7d/30d toggle + manual refresh |
| Smoketest | `/admin/api/pilot/smoketest` | On-demand POST; SSE-streamed results |
| Backup | `/admin/api/pilot/backup/download`, `/admin/api/pilot/backup/restore` | On-demand |
| Upgrade | `/admin/api/pilot/upgrade` | On-demand; fetched once on first render |

The main status poll runs every 5 seconds while the Station pane is visible
(backoff to 10 s / 30 s / 60 s on consecutive failures). Usage and Upgrade
cards fetch once on first render and then only on explicit refresh. Smoketest
and Backup are purely on-demand.

---

## Graduation model

Each feature in the extension is annotated in `CONTRACT.md` §12 with one of:

- **Station-permanent** — the feature depends on station-owned infrastructure
  (the data volume, the gateway supervisor, `state.db`) or station-specific
  deployment topology. It will never graduate upstream because upstream webui
  has no equivalent hook. These features stay in the extension indefinitely.

- **Upstream-candidate** — the feature is generic enough that it could be
  absorbed by hermes-webui upstream. A feature at this disposition is stable
  enough to propose upstream once the pilot validates under real use.

The graduation table in `CONTRACT.md` §12 is the authoritative record. Update
it whenever a new card ships.
