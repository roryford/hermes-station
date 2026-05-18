/* hermes-station admin extension — read-only Status pane injected into webui Settings. */
(function () {
  "use strict";
  const POLL_MS = 5000;
  const BACKOFFS = [5000, 10000, 30000, 60000];
  const DASH = "—";

  const menu = document.getElementById("settingsMenu");
  const main = document.querySelector(".settings-main");
  if (!menu || !main) return;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "side-menu-item";
  btn.dataset.settingsSection = "admin";
  btn.textContent = "Admin";
  btn.setAttribute("onclick", "switchSettingsSection('admin')");
  menu.appendChild(btn);

  const pane = document.createElement("div");
  pane.className = "settings-pane";
  pane.id = "settingsPaneAdmin";
  pane.innerHTML = '<div class="admin-empty">Loading status…</div>';
  main.appendChild(pane);

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
  function render(data) {
    pane.replaceChildren();
    const g = data.gateway || {}, w = data.webui || {}, p = data.provider || {}, m = data.memory || {};
    const gw = card("Gateway");
    appendDl(gw, [["State", titleCase(g.state)], ["PID", fmt(g.pid)], ["Uptime", fmtUptime(g.uptime_s)], ["Platform", fmt(g.platform)], ["Connection", fmt(g.connection)]]);
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
