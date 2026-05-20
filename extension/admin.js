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
