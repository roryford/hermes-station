/* hermes-station admin extension — read-only Status pane injected into webui Settings. */
(function () {
  "use strict";
  const POLL_MS = 5000;
  const BACKOFFS = [5000, 10000, 30000, 60000];
  const DASH = "—";

  const SECTION_ID = "station";
  const SECTION_LABEL = "Station";

  const menu = document.getElementById("settingsMenu");
  const main = document.querySelector(".settings-main");
  if (!menu || !main) return;

  // Build button with SVG icon matching webui's other settings items (activity/pulse icon).
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "side-menu-item";
  btn.dataset.settingsSection = SECTION_ID;
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", "16"); svg.setAttribute("height", "16");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor"); svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svgNS, "polyline");
  path.setAttribute("points", "22 12 18 12 15 21 9 3 6 12 2 12");
  svg.appendChild(path);
  const label = document.createElement("span");
  label.textContent = SECTION_LABEL;
  btn.appendChild(svg); btn.appendChild(label);
  menu.appendChild(btn);

  const pane = document.createElement("div");
  pane.className = "settings-pane";
  pane.id = "settingsPaneAdmin";
  pane.innerHTML = '<div class="admin-empty">Loading status…</div>';
  main.appendChild(pane);

  // webui's switchSettingsSection has a hardcoded allowlist of 6 sections; passing 'station'
  // falls back to 'conversation'. We override it so our section integrates as a peer.
  //
  // We must defend against three webui patterns that have clobbered prior wraps:
  //   1. panels.js declares `function switchSettingsSection(...)` at the top level, creating
  //      a non-configurable global. We therefore cannot install a getter/setter accessor.
  //   2. panels.js later does `switchSettingsSection = function (name) { _origSwitch(name); ... }`
  //      which captures whatever value is current at that moment. If we wrap before that line
  //      runs (script order), webui's wrap replaces ours.
  //   3. webui's async settings init reassigns the global again after DOMContentLoaded.
  //
  // Strategy: install our wrap, then make the property non-writable. Subsequent assignments
  // (`window.switchSettingsSection = ...`) become silent no-ops in sloppy mode, so our wrap
  // stays in front. We capture whatever was assigned just before our IIFE ran as _delegate
  // so non-station calls still fall through to webui's real implementation.
  // User-intent flag, decoupled from the .active class. Polling lifecycle is gated on
  // _userOpenedStation rather than the pane's .active class because webui's settings
  // init calls switchSettingsSection('conversation') at the end of its async load
  // (panels.js:5695), which routes through our wrap and clears .active — even when
  // the user is actively viewing Station. Class-based gating raced that init and
  // caused the gateway-restart test to flake (~1/4) by halting polling forever.
  //
  // The wrap CAN'T distinguish "user clicked Conversation" from "webui's init called
  // switchSettingsSection('conversation')" — they look identical. So we flip the flag
  // on a different signal: real click events on #settingsMenu menu items. webui's
  // init does not synthesize click events, only real user navigation does.
  let _userOpenedStation = false;
  let _delegate = window.switchSettingsSection;
  function activateStation() {
    document.querySelectorAll("#settingsMenu .side-menu-item").forEach((it) => {
      it.classList.toggle("active", it.dataset.settingsSection === SECTION_ID);
    });
    document.querySelectorAll(".settings-main .settings-pane").forEach((p) => {
      p.classList.toggle("active", p.id === "settingsPaneAdmin");
    });
  }
  function wrappedSwitchSettingsSection(name) {
    if (name === SECTION_ID) {
      _userOpenedStation = true;
      activateStation();
      start();
      return;
    }
    // Switching to a non-station section: webui's original only knows its 6 sections,
    // so it won't clear our .active. Clear it ourselves (visual correctness only —
    // polling lifecycle no longer depends on this class).
    pane.classList.remove("active");
    btn.classList.remove("active");
    if (typeof _delegate === "function") return _delegate.apply(this, arguments);
  }
  try {
    // The existing global was created via `function` declaration in panels.js, so it is
    // non-configurable. We can't install an accessor, but we CAN flip writable from true
    // to false on a non-configurable data property — that's an allowed descriptor change.
    Object.defineProperty(window, "switchSettingsSection", {
      value: wrappedSwitchSettingsSection,
      writable: false,
      configurable: false,
      enumerable: true,
    });
  } catch (_e) {
    // Fallback for runtimes where the property is freshly configurable (e.g. tests, or
    // future webui versions that switch to `let`/`const`). A plain assignment installs
    // the wrap but offers no clobber defense — webui upgrades may reintroduce the bug.
    window.switchSettingsSection = wrappedSwitchSettingsSection;
  }
  btn.addEventListener("click", () => window.switchSettingsSection(SECTION_ID));

  // Delegated click listener on the settings menu: real user clicks on menu items
  // flip _userOpenedStation. webui's async init does NOT synthesize click events,
  // so its switchSettingsSection('conversation') call at the end of loadSettingsPanel
  // cannot reach this handler — only genuine user navigation does. This is the
  // signal that distinguishes "user navigated away" from "webui init ran".
  menu.addEventListener("click", (ev) => {
    const target = ev.target && ev.target.closest ? ev.target.closest("[data-settings-section]") : null;
    if (!target) return;
    if (target.dataset.settingsSection === SECTION_ID) {
      _userOpenedStation = true;
      start();
    } else {
      _userOpenedStation = false;
      stop();
    }
  });

  async function fetchStatus() {
    if (typeof window.api === "function") return await window.api("/admin/api/pilot/status");
    const r = await fetch("/admin/api/pilot/status", { credentials: "include" });
    if (r.status === 401) {
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
      throw new Error("unauthorized");
    }
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  }

  const fmt = (v) => (v === null || v === undefined || v === "" ? DASH : String(v));
  const titleCase = (s) => (!s ? DASH : String(s).replace(/(^|[\s_-])(\w)/g, (_, p, c) => p + c.toUpperCase()));
  function fmtUptime(s) {
    if (s === null || s === undefined) return DASH;
    s = Math.max(0, Math.floor(Number(s) || 0));
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600;
    const m = Math.floor(s / 60);
    const parts = [];
    if (d) parts.push(d + "d");
    if (h || d) parts.push(h + "h");
    parts.push(m + "m");
    return parts.join(" ");
  }

  function card(title) {
    const s = document.createElement("section"); s.className = "admin-card";
    const h = document.createElement("h3"); h.textContent = title; s.appendChild(h);
    return s;
  }
  function appendDl(sec, pairs) {
    const dl = document.createElement("dl");
    for (const [k, v] of pairs) {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      dl.appendChild(dt); dl.appendChild(dd);
    }
    sec.appendChild(dl);
  }
  function renderChannels(sec, channels) {
    if (!Array.isArray(channels) || channels.length === 0) {
      const e = document.createElement("div"); e.className = "admin-empty"; e.textContent = "No channels reported."; sec.appendChild(e); return;
    }
    for (const ch of channels) {
      const row = document.createElement("div"); row.className = "admin-channel-row";
      const name = document.createElement("span"); name.className = "admin-channel-name"; name.textContent = fmt(ch && ch.name);
      const pill = document.createElement("span");
      pill.className = "admin-pill " + (ch && ch.ready ? "ok" : (ch && ch.intended ? "warn" : "muted"));
      pill.textContent = ch && ch.ready ? "ready" : (ch && ch.intended ? "intended" : "off");
      row.appendChild(name); row.appendChild(pill);
      if (ch && ch.reason) {
        const r = document.createElement("span"); r.className = "admin-reason"; r.textContent = ch.reason; row.appendChild(r);
      }
      sec.appendChild(row);
    }
  }
  async function restartGateway(button) {
    if (!window.confirm("Restart the gateway? In-flight requests will be dropped.")) return;
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "Restarting…";
    try {
      let payload;
      if (typeof window.api === "function") {
        payload = await window.api("/admin/api/pilot/gateway/restart", { method: "POST" });
      } else {
        const r = await fetch("/admin/api/pilot/gateway/restart", { method: "POST", credentials: "include" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        payload = await r.json();
      }
      if (payload && payload.ok) {
        if (typeof window.showToast === "function") {
          window.showToast("Gateway restarted.", 4000, "success");
        }
      } else {
        const msg = (payload && payload.error) || "restart failed";
        if (typeof window.showToast === "function") {
          window.showToast("Gateway restart failed: " + msg, 6000, "error");
        }
      }
    } catch (err) {
      if (typeof window.showToast === "function") {
        window.showToast("Gateway restart failed: " + (err && err.message ? err.message : err), 6000, "error");
      }
    } finally {
      button.disabled = false;
      button.textContent = originalText;
      // Re-poll status so the UI reflects the new gateway state.
      tick();
    }
  }

  // ── Usage card ────────────────────────────────────────────────────────────

  function fmtTokens(n) {
    if (n === null || n === undefined) return DASH;
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(n);
  }

  // State for the usage card (persisted across render() calls).
  let _usageDays = 7;
  let _usageData = null;
  let _usageLoading = false;

  async function _fetchUsage(days) {
    _usageLoading = true;
    _renderUsageCard();
    try {
      const r = await fetch("/admin/api/pilot/usage?days=" + days, { credentials: "include" });
      if (r.status === 401) {
        window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
        return;
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      _usageData = await r.json();
      _usageDays = days;
    } catch (_e) {
      _usageData = { _error: true };
    } finally {
      _usageLoading = false;
      _renderUsageCard();
    }
  }

  let _usageCardEl = null;

  function _renderUsageCard() {
    if (!_usageCardEl) return;
    // Wipe and rebuild the card body (keep the h3 header + controls row).
    while (_usageCardEl.children.length > 2) {
      _usageCardEl.removeChild(_usageCardEl.lastChild);
    }
    const body = document.createElement("div"); body.className = "admin-usage-body";
    if (_usageLoading) {
      const l = document.createElement("div"); l.className = "admin-empty"; l.textContent = "Loading…";
      body.appendChild(l);
    } else if (!_usageData || _usageData._error) {
      const e = document.createElement("div"); e.className = "admin-empty"; e.textContent = "Failed to load usage data.";
      body.appendChild(e);
    } else if (_usageData.no_db) {
      const e = document.createElement("div"); e.className = "admin-empty"; e.textContent = "No usage data yet — start a conversation first.";
      body.appendChild(e);
    } else {
      const s = _usageData.summary || {};
      const totalCost = s.total_cost || 0;
      const totalTokens = (s.input_tokens || 0) + (s.output_tokens || 0);
      const prefix = s.has_estimated ? "~" : "";
      const summary = document.createElement("div"); summary.className = "admin-usage-summary";
      summary.textContent = "Cost: " + prefix + "$" + totalCost.toFixed(4) + "  Tokens: " + fmtTokens(totalTokens) + "  API calls: " + (s.api_calls || 0);
      body.appendChild(summary);
      if (s.has_estimated) {
        const fn = document.createElement("div"); fn.className = "admin-usage-footnote"; fn.textContent = "~ estimated"; body.appendChild(fn);
      }
      const channels = _usageData.channels || [];
      if (channels.length) {
        const ch = document.createElement("div"); ch.className = "admin-usage-group";
        const ht = document.createElement("div"); ht.className = "admin-usage-group-title"; ht.textContent = "By channel"; ch.appendChild(ht);
        for (const row of channels) {
          const r = document.createElement("div"); r.className = "admin-usage-row";
          r.textContent = (row.source || "?") + "  $" + (row.cost || 0).toFixed(4) + "  " + fmtTokens(row.total_tokens) + " tokens  " + (row.api_calls || 0) + " calls";
          ch.appendChild(r);
        }
        body.appendChild(ch);
      }
      const models = _usageData.models || [];
      if (models.length) {
        const mg = document.createElement("div"); mg.className = "admin-usage-group";
        const mt = document.createElement("div"); mt.className = "admin-usage-group-title"; mt.textContent = "By model"; mg.appendChild(mt);
        for (const row of models) {
          const r = document.createElement("div"); r.className = "admin-usage-row";
          r.textContent = (row.model || "?") + " (" + (row.billing_provider || "?") + ")  $" + (row.cost || 0).toFixed(4) + "  " + fmtTokens(row.total_tokens) + " tokens";
          mg.appendChild(r);
        }
        body.appendChild(mg);
      }
    }
    _usageCardEl.appendChild(body);
  }

  function buildUsageCard() {
    const uc = card("Usage");

    // Controls row: [7d] [30d] [↻]
    const controls = document.createElement("div"); controls.className = "admin-card-actions";
    const btn7 = document.createElement("button"); btn7.type = "button"; btn7.className = "admin-btn" + (_usageDays === 7 ? " admin-btn-active" : ""); btn7.textContent = "7d";
    const btn30 = document.createElement("button"); btn30.type = "button"; btn30.className = "admin-btn" + (_usageDays === 30 ? " admin-btn-active" : ""); btn30.textContent = "30d";
    const btnR = document.createElement("button"); btnR.type = "button"; btnR.className = "admin-btn"; btnR.textContent = "↻";
    btnR.title = "Refresh";
    btn7.addEventListener("click", () => { btn7.className = "admin-btn admin-btn-active"; btn30.className = "admin-btn"; _fetchUsage(7); });
    btn30.addEventListener("click", () => { btn30.className = "admin-btn admin-btn-active"; btn7.className = "admin-btn"; _fetchUsage(30); });
    btnR.addEventListener("click", () => _fetchUsage(_usageDays));
    controls.appendChild(btn7); controls.appendChild(btn30); controls.appendChild(btnR);
    uc.appendChild(controls);

    _usageCardEl = uc;
    _renderUsageCard();

    // Auto-fetch on first render if we have no data yet.
    if (!_usageData && !_usageLoading) _fetchUsage(_usageDays);

    return uc;
  }

  // ── Backup card ───────────────────────────────────────────────────────────

  let _backupCardEl = null;
  let _backupInFlight = false;

  function _backupSetStatus(msg, isError) {
    if (!_backupCardEl) return;
    const area = _backupCardEl.querySelector(".admin-backup-status");
    if (area) { area.textContent = msg; area.className = "admin-backup-status" + (isError ? " admin-backup-error" : " admin-backup-ok"); }
  }

  async function _doDownload() {
    if (_backupInFlight) return;
    _backupInFlight = true;
    const dlBtn = _backupCardEl && _backupCardEl.querySelector(".admin-backup-dl");
    if (dlBtn) dlBtn.disabled = true;
    _backupSetStatus("Preparing backup…", false);
    try {
      const r = await fetch("/admin/api/pilot/backup/download", { method: "POST", credentials: "include" });
      if (r.status === 401) { window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname); return; }
      if (!r.ok) throw new Error("HTTP " + r.status);
      const cd = r.headers.get("content-disposition") || "";
      const m = cd.match(/filename="([^"]+)"/);
      const filename = m ? m[1] : "hermes-station-backup.tar.gz";
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = filename; a.style.display = "none";
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(url); document.body.removeChild(a); }, 1000);
      _backupSetStatus("Backup downloaded.", false);
      if (typeof window.showToast === "function") window.showToast("Backup downloaded.", 4000, "success");
    } catch (err) {
      _backupSetStatus("Download failed: " + (err && err.message ? err.message : err), true);
      if (typeof window.showToast === "function") window.showToast("Backup download failed: " + (err && err.message ? err.message : err), 6000, "error");
    } finally {
      _backupInFlight = false;
      if (dlBtn) dlBtn.disabled = false;
    }
  }

  async function _doRestore(fileInput) {
    if (_backupInFlight) return;
    const file = fileInput && fileInput.files && fileInput.files[0];
    if (!file) { _backupSetStatus("Please choose a backup file first.", true); return; }
    if (!window.confirm("Restore from backup? This will overwrite your current config and database. The gateway will restart.")) return;
    _backupInFlight = true;
    const restoreBtn = _backupCardEl && _backupCardEl.querySelector(".admin-backup-restore");
    if (restoreBtn) restoreBtn.disabled = true;
    _backupSetStatus("Uploading and restoring…", false);
    try {
      const fd = new FormData(); fd.append("backup_file", file);
      const r = await fetch("/admin/api/pilot/backup/restore", { method: "POST", credentials: "include", body: fd });
      if (r.status === 401) { window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname); return; }
      const payload = await r.json();
      if (payload && payload.ok) {
        const names = (payload.files || []).join(", ");
        _backupSetStatus("Restore complete: " + names, false);
        if (typeof window.showToast === "function") window.showToast("Restore complete.", 4000, "success");
      } else {
        const msg = (payload && payload.error) || "restore failed";
        _backupSetStatus("Restore failed: " + msg, true);
        if (typeof window.showToast === "function") window.showToast("Restore failed: " + msg, 6000, "error");
      }
    } catch (err) {
      _backupSetStatus("Restore failed: " + (err && err.message ? err.message : err), true);
      if (typeof window.showToast === "function") window.showToast("Restore failed: " + (err && err.message ? err.message : err), 6000, "error");
    } finally {
      _backupInFlight = false;
      if (restoreBtn) restoreBtn.disabled = false;
    }
  }

  function buildBackupCard() {
    const bc = card("Backup");

    const warn = document.createElement("div"); warn.className = "admin-backup-warn";
    warn.textContent = "Includes config, state DB, memories, pairings, and SOUL.md. API keys (.env) are not included — back those up separately.";
    bc.appendChild(warn);

    const dlActions = document.createElement("div"); dlActions.className = "admin-card-actions";
    const dlBtn = document.createElement("button"); dlBtn.type = "button"; dlBtn.className = "admin-btn admin-backup-dl"; dlBtn.textContent = "Download backup";
    dlBtn.addEventListener("click", _doDownload);
    dlActions.appendChild(dlBtn);
    bc.appendChild(dlActions);

    const restoreLabel = document.createElement("div"); restoreLabel.className = "admin-backup-restore-label"; restoreLabel.textContent = "Restore from backup:";
    bc.appendChild(restoreLabel);

    const restoreRow = document.createElement("div"); restoreRow.className = "admin-card-actions";
    const fileInput = document.createElement("input"); fileInput.type = "file"; fileInput.accept = ".tar.gz"; fileInput.className = "admin-backup-file";
    const restoreBtn = document.createElement("button"); restoreBtn.type = "button"; restoreBtn.className = "admin-btn admin-backup-restore"; restoreBtn.textContent = "Restore";
    restoreBtn.addEventListener("click", () => _doRestore(fileInput));
    restoreRow.appendChild(fileInput); restoreRow.appendChild(restoreBtn);
    bc.appendChild(restoreRow);

    const statusArea = document.createElement("div"); statusArea.className = "admin-backup-status";
    bc.appendChild(statusArea);

    _backupCardEl = bc;
    return bc;
  }

  // ── Topology card ─────────────────────────────────────────────────────────

  function buildTopologyCard() {
    const tc = card("Topology");
    const pre = document.createElement("pre");
    pre.style.cssText = "font-size:0.75rem;line-height:1.4;overflow-x:auto;background:rgba(0,0,0,0.15);border-radius:4px;padding:0.75rem;";
    pre.textContent = [
      "┌─────────────────────────────────────────┐",
      "│         hermes-station container         │",
      "│                                          │",
      "│  hermes-station  :8787 (public)          │",
      "│    ├── /admin   admin UI                 │",
      "│    └── /        → hermes-webui           │",
      "│                                          │",
      "│  hermes-webui    :8788 (internal)        │",
      "│                                          │",
      "│  hermes-agent    gateway subprocess      │",
      "│    ├── MCP servers                       │",
      "│    ├── channel listeners                 │",
      "│    └── /data/.hermes/state.db            │",
      "└─────────────────────────────────────────┘",
      "         │                   │",
      "   External APIs          Channels",
      "  (OpenRouter…)      (Telegram, Slack…)",
    ].join("\n");
    tc.appendChild(pre);
    appendDl(tc, [
      ["/data/.hermes/", "agent state (config, secrets, DB)"],
      ["/data/webui/", "WebUI sessions & signing key"],
      ["/data/workspace/", "user workspace files"],
    ]);
    return tc;
  }

  function render(data) {
    pane.replaceChildren();
    const g = data.gateway || {}, w = data.webui || {}, p = data.provider || {}, m = data.memory || {}, v = data.versions || {};
    const gw = card("Gateway");
    appendDl(gw, [["State", titleCase(g.state)], ["PID", fmt(g.pid)], ["Uptime", fmtUptime(g.uptime_s)], ["Platform", fmt(g.platform)], ["Connection", fmt(g.connection)]]);
    const actions = document.createElement("div"); actions.className = "admin-card-actions";
    const restartBtn = document.createElement("button");
    restartBtn.type = "button";
    restartBtn.className = "admin-btn";
    restartBtn.textContent = "Restart gateway";
    restartBtn.addEventListener("click", () => restartGateway(restartBtn));
    actions.appendChild(restartBtn);
    gw.appendChild(actions);
    pane.appendChild(gw);
    const wc = card("WebUI"); appendDl(wc, [["State", titleCase(w.state)], ["PID", fmt(w.pid)]]); pane.appendChild(wc);
    const pc = card("Provider"); appendDl(pc, [["Name", fmt(p.name)], ["Model", fmt(p.model)]]); pane.appendChild(pc);
    const cc = card("Channels"); renderChannels(cc, data.channels); pane.appendChild(cc);
    const mc = card("Memory");
    appendDl(mc, [["Provider", fmt(m.provider)], ["Ready", m.ready === true ? "Yes" : (m.ready === false ? "No" : DASH)]]);
    pane.appendChild(mc);
    const vc = card("Versions");
    appendDl(vc, [["Station", fmt(v.station)], ["WebUI", fmt(v.webui)], ["Hermes", fmt(v.hermes)]]);
    pane.appendChild(vc);
    pane.appendChild(buildUsageCard());
    pane.appendChild(buildBackupCard());
    pane.appendChild(buildTopologyCard());
  }

  let timer = null, failCount = 0, toastedThisBurst = false;
  // Polling lifecycle is gated on user intent + visibility, NOT on the pane's
  // .active class. See _userOpenedStation comment above for the rationale.
  const isActive = () => _userOpenedStation && document.visibilityState === "visible";

  async function tick() {
    if (!isActive()) return;
    try {
      const data = await fetchStatus();
      failCount = 0; toastedThisBurst = false;
      render(data);
    } catch (err) {
      failCount++;
      if (!toastedThisBurst) {
        toastedThisBurst = true;
        if (typeof window.showToast === "function") {
          window.showToast("Admin status load failed: " + (err && err.message ? err.message : err), 5000, "error");
        }
      }
    }
    schedule();
  }
  function schedule() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (!isActive()) return;
    const delay = failCount === 0 ? POLL_MS : BACKOFFS[Math.min(failCount - 1, BACKOFFS.length - 1)];
    timer = setTimeout(tick, delay);
  }
  function start() { if (!timer) tick(); }
  function stop() { if (timer) { clearTimeout(timer); timer = null; } }

  // No MutationObserver on .active: webui's settings init clears .active on its
  // own schedule (panels.js:5695 calls switchSettingsSection('conversation') at
  // the tail of loadSettingsPanel), which would falsely halt polling. Start/stop
  // is driven entirely by user-intent transitions in wrappedSwitchSettingsSection
  // and the menu click delegate, plus the visibilitychange listener below.
  document.addEventListener("visibilitychange", () => { isActive() ? start() : stop(); });
  if (isActive()) start();
})();
