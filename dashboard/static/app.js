/* dashboard/static/app.js — read-only render engine (STAGE v3).
 * Fetches JSON transport endpoints, renders into [data-bind] regions.
 * All injected values pass through esc(). No writes ever leave this file:
 * no POST/PUT/DELETE, no state mutation — fetch is GET-only. */
"use strict";
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
  });
}
function demoOn() {
  try {
    const stored = localStorage.getItem("demo");
    if (stored !== null) return stored === "1";
  } catch (e) {}
  return true; // default ON for this rebuild
}
async function getJSON(path) {
  const sep = path.indexOf("?") >= 0 ? "&" : "?";
  const url = demoOn() && path.indexOf("/api/") === 0 &&
    ["machine", "issues", "agents", "leads", "email", "llm", "logs"].some(function (k) {
      return path.indexOf("/api/" + k) === 0;
    }) ? path + sep + "demo=1" : path;
  const r = await fetch(url, {headers: {"Accept": "application/json"}});
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
function rel(ts, now) {
  if (!ts || !now) return "";
  const s = Math.max(0, Math.round((Date.parse(now) - Date.parse(ts)) / 1000));
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " d ago";
}
function chip(state) {
  const cls = {lit: "pass", ready: "amber", dormant: "amber", dark: "gray"}[state] || "gray";
  return '<span class="chip ' + cls + '">' + esc(state) + "</span>";
}
function copyBtn(value, label) {
  return '<button type="button" data-copy="' + esc(value) + '" aria-label="Copy ' +
    esc(label || value) + '">Copy</button>';
}
async function fillTopbar() {
  try {
    const m = await getJSON("/api/machine");
    document.querySelectorAll('[data-bind="kill"]').forEach(function (el) {
      el.textContent = "kill-switch: " + m.kill_switch;
      el.className = "kill " + (m.kill_switch === "released" ? "fail" : "pass");
    });
    return m;
  } catch (e) { return null; }
}
function markStale(view) {
  if (!view || !view.as_of) return;
  const head = (view.ledger_head && view.ledger_head.ts) || null;
  let stale = !!view.stale;
  document.querySelectorAll("[data-stale]").forEach(function (el) {
    if (stale) {
      el.innerHTML = '<div class="banner" role="alert">STALE — chain silent beyond threshold</div>';
    } else {
      el.innerHTML = "";
    }
  });
}
const renderers = {
  machine: async function (m) {
    const k = m.kpis;
    setText("kpi-lit", String(k.channels_lit));
    setText("kpi-lit-cap", "of " + Object.keys(m.channels).length + " configured channels");
    setText("kpi-chain", String(k.chain_length));
    setText("kpi-blockers", String(k.open_blockers));
    const d = m.outbox_drain;
    const arc = document.querySelector('[data-bind="arc"]');
    if (d.drain_pct == null) {
      arc.innerHTML = "<div class='empty'>Outbox empty — nothing queued. Drain arc appears with the first row.</div>";
    } else {
      const pct = d.drain_pct, len = (150.8 * pct / 100).toFixed(1);
      arc.innerHTML = "<div class='arcwrap'><svg width='120' height='70' viewBox='0 0 120 70'" +
        " role='img' aria-label='Outbox drain " + pct + " percent'>" +
        "<circle cx='60' cy='60' r='48' fill='none' stroke='#dde5dd' stroke-width='14'" +
        " stroke-dasharray='150.8 999' transform='rotate(180 60 60)'/>" +
        "<circle cx='60' cy='60' r='48' fill='none' stroke='#147a45' stroke-width='14'" +
        " stroke-dasharray='" + len + " 999' transform='rotate(180 60 60)'/></svg>" +
        "<div><strong class='mono'>" + pct + "%</strong>" +
        "<div class='muted'>" + d.done + "/" + d.total + " drained · " + d.inflight + " in flight</div></div></div>";
    }
    document.querySelector('[data-bind="channels"]').innerHTML = m.channel_order.map(function (c) {
      const ch = m.channels[c];
      const reason = ch.paused_reason == null ? "—" :
        (typeof ch.paused_reason === "object"
          ? Object.keys(ch.paused_reason).sort().map(function (k) {
              return k + ": " + (ch.paused_reason[k] || "ok");
            }).join("; ")
          : ch.paused_reason);
      return "<tr><td class='mono'>" + esc(c) + "</td><td>" + chip(ch.state) +
        "</td><td class='mono muted'>" + esc(reason) + "</td></tr>";
    }).join("");
  },
  "agent-queue": async function (v) {
    document.querySelector('[data-bind="agent-live"]').innerHTML = v.live.length ? v.live.map(function (x) {
      return "<tr><td class='mono'>" + esc(x.agent) + "</td><td>" + chip("lit") +
        "</td><td class='mono'>" + esc(x.latest_event) + "</td><td class='muted'>" +
        esc(x.latest_ts) + " — " + esc(rel(x.latest_ts, v.as_of)) + "</td></tr>";
    }).join("") : "<tr><td colspan='4'><div class='empty'>No agents active — rows appear here the second an agent acts. This is the live view; the registry lives under Agents.</div></td></tr>";
  },
  agents: async function (v) {
    document.querySelector('[data-bind="actors"]').innerHTML = v.actors.length ? v.actors.map(function (x) {
      return "<tr><td class='mono'>" + esc(x.actor) + "</td><td>" + x.events +
        "</td><td class='mono'>" + esc(x.latest_event) + "</td><td class='muted'>" +
        esc(x.latest_ts) + " — " + esc(rel(x.latest_ts, v.as_of)) + "</td></tr>";
    }).join("") : "<tr><td colspan='4'><div class='empty'>No actors yet — the chain is empty.</div></td></tr>";
    document.querySelector('[data-bind="verdicts"]').innerHTML = v.note_verdicts.map(function (x) {
      return "<tr><td class='mono'>" + esc(x.note) + "</td><td>" + esc(x.verdict) + "</td></tr>";
    }).join("");
    document.querySelector('[data-bind="registry"]').innerHTML = v.persona_registry.length ? v.persona_registry.map(function (x) {
      return "<tr><td class='mono'>" + esc(x.agent_key) + "</td><td>" + esc(x.role_desc) +
        "</td><td>" + esc(x.active) + "</td></tr>";
    }).join("") : "<tr><td colspan='3'><div class='empty'>No personas registered — registry fills when agents land.</div></td></tr>";
  },
  orchestrator: async function (v) {
    document.querySelector('[data-bind="standing"]').innerHTML =
      "<p>kill-switch: <strong>" + esc(v.kill_switch) + "</strong></p>";
    document.querySelector('[data-bind="gates"]').innerHTML = v.open_gates.map(function (g) {
      return "<tr><td class='mono'>" + esc(g) + "</td><td><span class='chip amber'>open</span></td></tr>";
    }).join("");
    const last = v.last_close;
    document.querySelector('[data-bind="last-close"]').innerHTML = last ?
      "<p class='mono'>#" + last.audit_id + " " + esc(last.event) + " — " + esc(last.exit_reason) + "</p>" :
      "<div class='empty'>No traversal recorded yet.</div>";
    document.querySelector('[data-bind="timeline"]').innerHTML = v.cycle_timeline.map(function (t) {
      return "<tr><td class='mono'>" + esc(t.day) + "</td>" +
        "<td><div class='bar' role='img' aria-label='" + t.events + " events'>" +
        "<i style='width:" + Math.min(100, t.events * 10) + "%'></i></div></td><td>" + t.events + "</td></tr>";
    }).join("");
  },
  logs: async function (v) {
    const q = new URLSearchParams(location.search);
    const url = "/api/logs?limit=100" + (q.get("event") ? "&event=" + encodeURIComponent(q.get("event")) : "");
    const data = await getJSON(url);
    document.querySelector('[data-bind="loglines"]').textContent = data.lines.length ? data.lines.map(function (x) {
      return "#" + x.id + " " + x.ts + " " + x.actor + " " + x.event;
    }).join("\n") : "No log lines — the chain is empty.";
  },
  llm: async function (v) {
    const keys = Object.keys(v.per_model).sort();
    const max = Math.max.apply(null, [1].concat(keys.map(function (k) {
      return v.per_model[k].prompt + v.per_model[k].completion;
    })));
    document.querySelector('[data-bind="usage"]').innerHTML = keys.length ? keys.map(function (k) {
      const s = v.per_model[k], tot = s.prompt + s.completion;
      const w = Math.max(2, Math.round(100 * tot / max));
      return "<tr><td class='mono'>" + esc(k) + "</td>" +
        "<td><div class='bar' role='img' aria-label='" + tot + " tokens' title='" +
        esc(s.prompt + " in / " + s.completion + " out / $" + s.cost.toFixed(5)) + "'>" +
        "<i style='width:" + w + "%'></i></div></td>" +
        "<td class='mono'>" + s.prompt + "/" + s.completion + " · $" + s.cost.toFixed(5) +
        " · " + s.calls + " calls</td></tr>";
    }).join("") : "<tr><td><div class='empty'>No metered calls yet.</div></td></tr>";
    document.querySelector('[data-bind="dormant"]').innerHTML = v.dormant_channels.map(function (c) {
      return "<tr><td class='mono'>" + esc(c) + "</td>" +
        "<td><div class='bar hatched' role='img' aria-label='dormant, zero spend'>" +
        "<i style='width:100%'></i></div></td><td class='muted'>dormant — $0.00</td></tr>";
    }).join("");
    document.querySelector('[data-bind="smokes"]').innerHTML = v.smoke_history.length ? v.smoke_history.map(function (s) {
      return "<tr><td>#" + s.audit_id + "</td><td class='mono'>" + esc(s.channel) +
        "</td><td class='muted'>" + esc(s.ts) + " — " + esc(rel(s.ts, v.as_of)) + "</td></tr>";
    }).join("") : "<tr><td><div class='empty'>No smokes recorded.</div></td></tr>";
  },
  providers: async function (v) {
    document.querySelector('[data-bind="counts"]').innerHTML =
      "<p><span class='chip gray'>Total " + v.counts.total + "</span> " +
      "<span class='chip pass'>Configured " + v.counts.configured + "</span> " +
      "<span class='chip gray'>Dark " + v.counts.dark + "</span> " +
      "<span class='chip amber'>In rotation " + v.counts.in_rotation + "</span></p>";
    document.querySelector('[data-bind="providers"]').innerHTML = v.providers.map(function (c) {
      return "<tr><td class='mono'>" + esc(c.provider) + "</td>" +
        "<td><span class='chip " + (c.configured ? "pass" : "gray") + "'>" +
        (c.configured ? "configured" : "dark") + "</span></td>" +
        "<td>" + (c.in_rotation ? "in rotation" : "dormant") + "</td>" +
        "<td class='muted'>" + esc(c.allowlist) + "</td></tr>";
    }).join("");
  },
  email: async function (v) {
    const cls = {LIVE: "pass", "WIRED-IDLE": "amber", "NOT-WIRED": "gray"};
    document.querySelector('[data-bind="caps"]').innerHTML = v.capabilities.map(function (c) {
      return "<tr><td class='mono'>" + c.n + "</td><td>" + esc(c.capability.slice(0, 90)) +
        "</td><td><span class='chip " + (cls[c.status] || "gray") + "'>" + c.status + "</span></td></tr>";
    }).join("");
    document.querySelector('[data-bind="sends"]').innerHTML =
      "<div class='empty'>" + esc(v.sends_today.note) + ".</div>";
    document.querySelector('[data-bind="idents"]').innerHTML = v.identities.map(function (i) {
      return "<tr><td class='mono'>" + esc(i.identity) + "</td><td>" +
        (i.configured ? "configured" : "dark") + "</td></tr>";
    }).join("");
  },
  leads: async function (v, params) {
    const q = new URLSearchParams(location.search);
    let url = "/api/leads";
    const parts = [];
    ["q", "stage", "lead_id"].forEach(function (k) {
      if (q.get(k)) parts.push(k + "=" + encodeURIComponent(q.get(k)));
    });
    const data = await getJSON(url + (parts.length ? "?" + parts.join("&") : ""));
    document.querySelector('[data-bind="leads"]').innerHTML = data.leads.length ? data.leads.map(function (x) {
      return "<tr><td class='mono'>#" + x.id + "</td><td class='mono'>" + esc(x.email) +
        "</td><td><span class='chip amber'>" + esc(x.stage) + "</span></td></tr>";
    }).join("") : "<tr><td colspan='3'><div class='empty'>No leads match — upload a list to fill this board.</div></td></tr>";
    const hist = document.querySelector('[data-bind="history"]');
    if (data.history) {
      hist.innerHTML = data.history.threads.map(function (t) {
        return "<div class='card'><h2>Thread #" + t.id + "</h2>" + (t.messages.length ? t.messages.map(function (m) {
          return "<div class='mono'>[" + esc(m.direction) + "] " + esc((m.body_raw || "").slice(0, 120)) + "</div>";
        }).join("") : "<div class='empty'>No messages.</div>") + "</div>";
      }).join("");
    } else { hist.innerHTML = ""; }
  },
  ledger: async function (v) {
    document.querySelector('[data-bind="entries"]').innerHTML = v.entries.length ? v.entries.map(function (e) {
      return "<tr><td class='mono'>#" + e.id + "</td><td class='mono'>" + esc(e.ts) +
        "</td><td class='mono'>" + esc(e.event) + "</td>" +
        "<td><button type='button' data-copy='" + e.id + "' aria-label='Copy audit id " + e.id + "'>Copy</button></td></tr>";
    }).join("") : "<tr><td><div class='empty'>Chain empty — outreach dark, awaiting creds.</div></td></tr>";
    document.querySelector('[data-bind="spend"]').textContent = JSON.stringify(v.spend_totals);
  },
  compliance: async function (v) {
    document.querySelector('[data-bind="posture"]').innerHTML =
      "<p>kill-switch: <strong>" + esc(v.kill_switch) + "</strong></p>";
    document.querySelector('[data-bind="scans"]').textContent =
      "dirty scan files: " + JSON.stringify(v.placeholder_scan_dirty) +
      " · live forbidden tokens: " + JSON.stringify(v.live_forbidden_tokens);
  },
  queue: async function (v) {
    document.querySelector('[data-bind="queue"]').innerHTML = v.items.map(function (i) {
      return "<tr><td>" + esc(i.item) + "</td><td>" + esc(i.status) +
        "</td><td><button type='button' data-copy='" + esc(i.item) +
        "' aria-label='Copy " + esc(i.item) + "'>Copy</button></td></tr>";
    }).join("");
  }
};
function setText(bind, text) {
  document.querySelectorAll('[data-bind="' + bind + '"]').forEach(function (el) {
    el.textContent = text;
  });
}
document.addEventListener("click", function (e) {
  const b = e.target.closest("[data-copy]");
  if (b && navigator.clipboard) navigator.clipboard.writeText(b.getAttribute("data-copy"));
});
document.querySelectorAll("form[data-bot-form]").forEach(function (form) {
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    const q = new FormData(form).get("q") || "";
    const box = form.parentElement.querySelector("[data-bot-answer]");
    try {
      const data = await getJSON("/api/bot?q=" + encodeURIComponent(q));
      box.innerHTML = "<div class='card'><p>" + esc(data.answer) + "</p>" +
        "<p class='muted'>sources: " + esc(JSON.stringify(data.sources)) + "</p></div>";
    } catch (err) { box.textContent = "view failed"; }
  });
});
document.querySelectorAll("form[data-logs-form], form[data-leads-form]").forEach(function (form) {
  form.addEventListener("submit", function (e) { e.preventDefault(); location.reload(); });
});
document.addEventListener("keydown", function (e) {
  const typing = document.activeElement && document.activeElement.tagName === "INPUT";
  if (e.key === "r" && !typing) location.reload();
  if (e.key === "/" && !typing) {
    e.preventDefault();
    const q = document.querySelector("input[name=q]");
    if (q) q.focus();
  }
  if (e.key === "Escape" && document.activeElement) document.activeElement.blur();
});
(async function boot() {
  const page = document.body.getAttribute("data-page");
  const m = await fillTopbar();
  if (!page || !renderers[page]) return;
  try {
    const data = await getJSON("/api/" + page + location.search);
    await renderers[page](data);
    markStale(data);
  } catch (e) {
    document.querySelector("main").insertAdjacentHTML("afterbegin",
      "<div class='banner' role='alert'>view failed to load data.</div>");
  }
})();
/* M1: status pill, bell, demo switch, kill-switch flow, Machine blocks A-E. */
document.querySelectorAll('[data-demo-switch]').forEach(function (box) { box.checked = demoOn(); box.addEventListener('change', function () { try { localStorage.setItem('demo', box.checked ? '1' : '0'); } catch (e) {} location.reload(); }); });
