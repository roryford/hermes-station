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
  const _origSwitch = window.switchSettingsSection;
  function activateStation() {
    document.querySelectorAll("#settingsMenu .side-menu-item").forEach((it) => {
      it.classList.toggle("active", it.dataset.settingsSection === SECTION_ID);
    });
    document.querySelectorAll(".settings-main .settings-pane").forEach((p) => {
      p.classList.toggle("active", p.id === "settingsPaneAdmin");
    });
  }
  window.switchSettingsSection = function (name) {
    if (name === SECTION_ID) { activateStation(); return; }
    // Switching to a non-station section: webui's original only knows its 6 sections,
    // so it won't clear our .active. Clear it ourselves.
    pane.classList.remove("active");
    btn.classList.remove("active");
    if (typeof _origSwitch === "function") return _origSwitch.apply(this, arguments);
  };
  btn.addEventListener("click", () => window.switchSettingsSection(SECTION_ID));

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
    const g = data.gateway || {}, w = data.webui || {}, p = data.provider || {}, m = data.memory || {};
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
  }

  let timer = null, failCount = 0, toastedThisBurst = false;
  const isActive = () => pane.classList.contains("active") && document.visibilityState === "visible";

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

  new MutationObserver(() => { isActive() ? start() : stop(); }).observe(pane, { attributes: true, attributeFilter: ["class"] });
  document.addEventListener("visibilitychange", () => { isActive() ? start() : stop(); });
  if (isActive()) start();
})();
