/* dashboard/static/machine.js — M1 Machine blocks + status/kill/demo flows.
 * GET-only except the kill-switch POST (write channel 1 of 3). */
"use strict";
(function () {
  function demoSwitch() {
    document.querySelectorAll("[data-demo-switch]").forEach(function (box) {
      box.checked = demoOn();
      box.addEventListener("change", function () {
        try { localStorage.setItem("demo", box.checked ? "1" : "0"); } catch (e) {}
        location.reload();
      });
    });
    if (demoOn()) {
      document.querySelectorAll("[data-demo-pill]").forEach(function (el) {
        el.innerHTML = '<span class="demopill">DEMO DATA</span>';
      });
    }
  }
  async function status() {
    let data = null;
    try { data = await getJSON("/api/status"); } catch (e) { return null; }
    const pill = document.querySelector('[data-bind="status-pill"]');
    if (pill && data) {
      pill.textContent = data.state.charAt(0).toUpperCase() + data.state.slice(1);
      pill.className = "statuspill " + data.state;
    }
    const bell = document.querySelector("[data-bell]");
    const list = document.querySelector("[data-bell-list]");
    if (bell && data) {
      try {
        const payload = await getJSON("/api/issues");
        const issues = Array.isArray(payload) ? payload : (payload.issues || []);
        bell.textContent = "(! " + issues.length + ")";
        bell.setAttribute("aria-label", "Backend issues: " + issues.length);
        list.innerHTML = issues.length ? issues.map(function (i) {
          return "<div><strong>" + esc(i.severity) + "</strong> " + esc(i.title || i.message || "") +
            " <span class='muted'>" + esc(i.component) + "</span></div>";
        }).join("") : "<div class='muted'>No active issues.</div>";
        bell.onclick = function () {
          const open = list.hidden;
          list.hidden = !open;
          bell.setAttribute("aria-expanded", String(open));
        };
      } catch (e) {}
    }
    const banner = document.querySelector("[data-stop-banner]");
    if (banner && data && data.stop && data.stop.stopped) {
      const reason = (data.stop.reason || "").replace(/\.+$/, "");
      const at = data.stop.at ? " · " + data.stop.at : "";
      banner.innerHTML = "<div class='stopbanner' role='alert'>SYSTEM STOPPED. " +
        "Sending and new agent tasks are halted. Reason: " + esc(reason) +
        ". " + esc(data.stop.by) + esc(at) +
        " <button type='button' data-release>Release stop</button></div>";
      const rel = banner.querySelector("[data-release]");
      if (rel) rel.addEventListener("click", releaseFlow);
    }
    return data;
  }
  function modal(html) {
    const wrap = document.createElement("div");
    wrap.className = "modalwrap";
    wrap.innerHTML = "<div class='modal' role='dialog' aria-modal='true'>" + html + "</div>";
    document.body.appendChild(wrap);
    const close = function () { wrap.remove(); };
    wrap.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    const cancel = wrap.querySelector("[data-cancel]");
    if (cancel) cancel.addEventListener("click", close);
    return wrap;
  }
  async function postKill(action, reason) {
    const r = await fetch("/api/kill-switch", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, reason: reason})
    });
    if (r.status === 409) {
      const data = await r.json();
      return {failed: data.checks.filter(function (c) { return !c.ok; })};
    }
    if (!r.ok) throw new Error("HTTP " + r.status);
    return {ok: true};
  }
  function engageFlow() {
    const wrap = modal("<h2>Stop the entire system?</h2>" +
      "<p>All sending stops immediately. Queued emails are held, not deleted. " +
      "Agents finish their current step, then go idle. Nothing resumes until you release the stop.</p>" +
      "<label class='field' for='k-reason'>Reason (optional)</label>" +
      "<input id='k-reason' type='text' value='Operator stop'>" +
      "<div class='row'><button type='button' data-cancel>Cancel</button>" +
      "<button type='button' class='estop' data-confirm>Stop system</button></div>" +
      "<div data-err role='alert'></div>");
    wrap.querySelector("[data-confirm]").addEventListener("click", async function () {
      const reason = wrap.querySelector("#k-reason").value || "Operator stop";
      try {
        await postKill("engage", reason);
        location.reload();
      } catch (e) {
        wrap.querySelector("[data-err]").innerHTML =
          "<p class='fail' style='color:var(--red)'>Stop not confirmed. Retry.</p>";
      }
    });
  }
  async function releaseFlow() {
    let checks = [];
    try {
      const st = await getJSON("/api/status");
      checks = st.preflight || [];
    } catch (e) {
      checks = [];
    }
    const items = checks.length ? checks.map(function (c) {
      return "<li class='" + (c.ok ? "pass" : "fail") + "'>" + esc(c.check) + " — " +
        esc(c.ok ? "pass" : "FAIL: " + c.detail) + "</li>";
    }).join("") : "<li>pre-flight unreachable — cannot verify</li>";
    const blocked = checks.length > 0 && checks.some(function (c) {
      return !c.ok && c.check !== "suppression list loaded";
    });
    const wrap = modal("<h2>Resume operations?</h2><ul class='preflight'>" + items + "</ul>" +
      "<div class='row'><button type='button' data-cancel>Cancel</button>" +
      "<button type='button' class='primary' data-confirm" +
      (blocked ? " disabled title='Resolve failing checks first'" : "") + ">Release</button></div>");
    if (blocked) return;
    wrap.querySelector("[data-confirm]").addEventListener("click", async function () {
      try {
        await postKill("release", "Operator release");
        location.reload();
      } catch (e) {
        location.reload();
      }
    });
  }
  function renderMachine(m) {
    const b = m.banner;
    const banner = document.querySelector('[data-bind="status-banner"]');
    if (banner) {
      banner.innerHTML = "<div class='card'><h2>" + esc(b.sentence) + "</h2>" +
        "<p class='muted'>" + esc(b.subline) + "</p></div>";
    }
    const pipe = document.querySelector('[data-bind="pipeline"]');
    if (pipe) {
      pipe.innerHTML = m.pipeline.map(function (s, i, arr) {
        const conv = i > 0 && arr[i - 1].count ? " → " +
          Math.round(100 * (s.count || 0) / arr[i - 1].count) + "%" : "";
        const waiting = s.stalled ? " <span class='chip amber'>" + s.stalled + " waiting</span>" : "";
        return "<div class='pipestage" + (s.stalled ? " amber" : "") + "'><div class='muted'>" +
          esc(s.stage) + "</div><div class='n'>" + (s.count == null ? "—" : s.count) + "</div>" +
          "<div class='muted'>" + (s.delta == null ? "—" : "▲ " + s.delta + "%") + conv + "</div>" +
          "<div class='muted'>" + s.queued + " queued</div>" + waiting + "</div>";
      }).join("");
    }
    const k = m.kpis;
    const tiles =
      "<div class='card'><div class='muted'>Sent today</div><div class='kpi'>" + k.sent_today.value +
      (k.sent_today.cap != null ? " / " + k.sent_today.cap : "") + "</div>" +
      (k.sent_today.cap != null
        ? "<div class='bar' role='img'><i style='width:" +
          Math.round(100 * k.sent_today.value / k.sent_today.cap) + "%'></i></div>" : "") +
      "</div>" +
      "<div class='card'><div class='muted'>Reply rate, 7 days</div><div class='kpi'>" +
      (k.reply_rate_7d.value == null ? "—" : k.reply_rate_7d.value + "%") + "</div>" +
      "<div class='kpi-cap'>" +
      (k.reply_rate_7d.delta_pts == null ? "no baseline yet" : "▲ " + k.reply_rate_7d.delta_pts + " pts vs previous 7 days") +
      "</div></div>" +
      "<div class='card'><div class='muted'>Meetings booked, 7 days</div><div class='kpi'>" +
      (k.meetings_7d.value == null ? "—" : k.meetings_7d.value) + "</div>" +
      "<div class='kpi-cap'>" +
      (k.meetings_7d.target == null ? "no target set" : "target " + k.meetings_7d.target) + "</div></div>" +
      "<div class='card'><div class='muted'>Deliverability</div><div class='kpi'>" +
      (k.deliverability.bounce == null ? "—" : "Bounce " + k.deliverability.bounce + "%") + "</div>" +
      "<div class='kpi-cap'>limits " + k.deliverability.bounce_limit + "% / " +
      k.deliverability.complaint_limit + "%" +
      (k.deliverability.bounce != null && k.deliverability.bounce >= k.deliverability.bounce_limit
        ? " <span class='chip amber'>Attention</span>" : " <span class='chip pass'>Healthy</span>") +
      "</div></div>";
    const grid = document.querySelector('[data-bind="kpis"]');
    if (grid) grid.innerHTML = tiles;
    const act = document.querySelector('[data-bind="activity"]');
    if (act) {
      act.innerHTML = m.activity.length ? m.activity.map(function (a) {
        return "<div><span class='mono'>" + esc(a.at) + "</span> <strong>" + esc(a.agent) +
          "</strong> — " + esc(a.text) +
          (a.lead ? " (<span class='mono'>" + esc(a.lead) + "</span>)" : "") + "</div>";
      }).join("") : "<div class='empty'>No activity yet.</div>";
    }
    const att = document.querySelector('[data-bind="attention"]');
    if (att) {
      att.innerHTML = m.attention.length ? m.attention.map(function (i) {
        return "<div><span class='chip " + (i.severity === "critical" ? "fail" : "amber") + "'>" +
          esc(i.severity) + "</span> <strong>" + esc(i.title) + "</strong> " +
          "<span class='mono muted'>" + esc(i.component) + "</span>" +
          "<div class='muted'>since " + esc(i.since) + " · " + i.count + " occurrences " +
          "<button type='button' data-copy='" + esc(i.id) + "'>Copy details</button></div></div>";
      }).join("") : "<div class='empty'>✓ No active issues.</div>";
    }
    const comp = document.querySelector('[data-bind="components"]');
    if (comp) {
      comp.innerHTML = m.components.map(function (c) {
        return "<div class='card'><div><span class='chip " + (c.ok ? "pass" : "fail") + "'>" +
          (c.ok ? "ok" : "down") + "</span> <strong>" + esc(c.name) + "</strong></div>" +
          "<div class='muted'>" + esc(c.metric) + "</div></div>";
      }).join("");
    }
  }
  document.querySelectorAll("[data-kill]").forEach(function (btn) {
    btn.addEventListener("click", engageFlow);
  });
  demoSwitch();
  (async function () {
    await status();
    if (!document.querySelector('[data-bind="pipeline"]')) return;
    try {
      const m = await getJSON("/api/machine");
      renderMachine(m);
    } catch (e) {
      document.querySelector('[data-bind="pipeline"]').innerHTML =
        "<div class='empty'>Machine data failed to load. <button type='button' onclick='location.reload()'>Retry</button></div>";
    }
  })();
})();
