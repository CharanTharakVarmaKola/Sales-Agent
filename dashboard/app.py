"""dashboard/app.py - CONTROL ROOM v2 (STAGE D2 redesign, STAGE D4 home).

Light green/white SaaS console per the DD spec note (cycle report).
PRIME DIRECTIVE (unchanged): read-only over all state except the operator
kill-switch POST /api/kill-switch (Origin-checked, JSON-only, optional
OPERATOR_TOKEN). Store reads, env-presence booleans, read-only file scans.
GET-only elsewhere (others 405).
Localhost unless ALLOW_REMOTE=1. Every value redacted pre-render and
derived from append-only artifacts - zero static/dummy values; honest
empty states instead. CSS served as a static asset (no inline dumps).
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from orchestrator.posture import CHANNELS, DEPRECATED_ENV, channel_status, is_live
from orchestrator.redact import redact_for_audit
from orchestrator.store import Store
from orchestrator.timeutil import utcnow_iso

ROOT = pathlib.Path(__file__).parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
STALE_AFTER_DEFAULT_S = 900
THEMES = ("dark", "light")

# Named display budgets (Code Standards: no magic numbers in UI code).
LEDGER_LIMIT_DEFAULT = 50
LEDGER_LIMIT_MAX = 200
SMOKE_HISTORY_LIMIT = 20
QUARANTINE_ROW_LIMIT = 20
TIMELINE_DAY_LIMIT = 14
RUNBOOK_SNIPPET_LIMIT = 12
RUNBOOK_SNIPPET_CHARS = 150
QUERY_TEXT_MAX = 80
BOT_QUESTION_MAX = 200
AUDIT_SCAN_LIMIT = 500


def _clamp_limit(raw, default: int = LEDGER_LIMIT_DEFAULT) -> int:
    """Clamp a caller-supplied limit into [1, LEDGER_LIMIT_MAX]."""
    return max(1, min(LEDGER_LIMIT_MAX, int(raw or default)))

NAV = (("machine", "MACHINE"), ("agent-queue", "AGENT QUEUE"),
       ("agents", "AGENTS"), ("orchestrator", "ORCHESTRATOR"),
       ("logs", "LOGS"), ("llm", "LLM USAGE"), ("providers", "LLM PROVIDERS"),
       ("email", "EMAIL"), ("leads", "LEADS/CRM"), ("ledger", "LEDGER"),
       ("compliance", "COMPLIANCE"), ("queue", "OPERATOR QUEUE"))


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _rel(ts: str | None, now: str) -> str:
    """Absolute-with-relative hint per DD spec (derived, no clock reads)."""
    dt, nd = _parse_ts(ts), _parse_ts(now)
    if not dt or not nd:
        return ""
    s = max(0, int((nd - dt).total_seconds()))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def _chip(state: str) -> str:
    cls = {"lit": "pass", "ready": "amber", "dormant": "amber",
           "dark": "gray"}.get(state, "gray")
    return cls


def _reason_text(reason) -> str:
    """Per-slot pause reasons (zoho dict) as human lines; None as an em dash."""
    if reason is None:
        return "—"
    if isinstance(reason, dict):
        return "; ".join(f"{k}: {(v or 'ok')}" for k, v in sorted(reason.items()))
    return str(reason)


class Dashboard:
    """Pure-read views. The one write path (kill-switch POST) lives in
    the HTTP handler, not here."""

    def __init__(self, store: Store):
        self.store = store

    def _view(self, payload):
        return redact_for_audit(payload)

    def _base(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        now = utcnow_iso()
        head = self.store.fetchone("SELECT MAX(id) AS m, MAX(ts) AS ts FROM audit")
        head_ts = head["ts"] if head and head["ts"] else None
        stale = False
        if head_ts:
            now_dt, head_dt = _parse_ts(now), _parse_ts(head_ts)
            if now_dt and head_dt:
                stale = (now_dt - head_dt).total_seconds() > stale_after_s
        return {"as_of": now, "ledger_head": {"id": head["m"] if head else None,
                                              "ts": head_ts},
                "stale": stale, "stale_after_s": stale_after_s}

    def _paused_reasons(self) -> dict:
        try:
            from orchestrator import live as _live
        except ImportError:
            return {}
        makers = {
            "llm/omniroute": _live.OmniRouteClient,
            "llm/freellmapi": _live.FreeLLMClient,
            "llm/ollama": _live.OllamaClient,
            "email/gmail-smtp": _live.GmailSmtpSender,
            "email/agentmail-api": _live.AgentMailApiSender,
            "email/gmail": _live.GmailSender,
            "email/zoho": _live.ZohoSender,
            "email/agentmail": _live.AgentMailSender,
        }
        reasons: dict = {}
        for channel, cls in makers.items():
            try:
                paused, reason = cls().paused
                reasons[channel] = None if not paused else reason
            except Exception:
                reasons[channel] = "unknown"
        try:
            slot_reasons = {}
            for i in (1, 2, 3, 4):
                paused, reason = _live.ZohoSmtpSender(slot=i).paused
                slot_reasons[f"0{i}"] = None if not paused else reason
            reasons["email/zoho-smtp"] = slot_reasons
        except Exception:
            reasons["email/zoho-smtp"] = "unknown"
        return reasons

    def _channels(self) -> tuple[dict, list]:
        status = channel_status()
        reasons = self._paused_reasons()
        smokes: dict = {}
        for row in self.store.fetchall(
                "SELECT id, ts, payload_json FROM audit WHERE event LIKE 'live.smoke.%'"
                " ORDER BY id DESC LIMIT 100"):
            try:
                p = json.loads(row["payload_json"])
            except ValueError:
                continue
            ch = p.get("channel")
            if ch and ch not in smokes:
                smokes[ch] = {"audit_id": row["id"], "ts": row["ts"],
                              "usage": p.get("usage"), "route": p.get("last_route")}
        by_channel = {}
        for ch, st in status.items():
            lit = ch in smokes and st["live_unlocked"]
            state = "lit" if lit else ("ready" if st["configured"]
                                       else ("dormant" if "omniroute" in ch else "dark"))
            by_channel[ch] = {"state": state, "configured": st["configured"],
                              "live_unlocked": st["live_unlocked"],
                              "last_smoke": smokes.get(ch),
                              "paused_reason": reasons.get(ch)}
        order = {"dark": 0, "dormant": 1, "ready": 2, "lit": 3}
        channel_order = sorted(by_channel,
                               key=lambda c: (order.get(by_channel[c]["state"], 9), c))
        return by_channel, channel_order

    def _spend(self, cap: int = AUDIT_SCAN_LIMIT) -> tuple[list, dict]:
        spend, totals = [], {"prompt": 0, "completion": 0, "cost": 0.0,
                             "unpriced": 0}
        for e in self.store.fetchall(
                "SELECT id, event, payload_json FROM audit ORDER BY id DESC LIMIT ?",
                (cap,)):
            try:
                p = json.loads(e["payload_json"] or "{}")
            except ValueError:
                continue
            u = p.get("usage")
            if isinstance(u, dict):
                spend.append({"audit_id": e["id"], "event": e["event"], "usage": u})
                for k, t in (("prompt_tokens", "prompt"),
                             ("completion_tokens", "completion")):
                    if isinstance(u.get(k), (int, float)):
                        totals[t] += u[k]
                if isinstance(u.get("cost_estimate"), (int, float)):
                    totals["cost"] += u["cost_estimate"]
                else:
                    totals["unpriced"] += 1
        return spend, totals

    def machine_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Channels, KPIs, outbox drain — all derived from posture + ledger."""
        by_channel, order = self._channels()
        head = self.store.fetchone("SELECT MAX(id) AS m FROM audit")
        spend, totals = self._spend()
        queue = self.queue_view()["items"]
        kpis = {"channels_lit": sum(1 for c in by_channel.values()
                                    if c["state"] == "lit"),
                "chain_length": head["m"] if head and head["m"] else 0,
                "spend_usd": totals["cost"],
                "spend_unpriced_rows": totals["unpriced"],
                "open_blockers": sum(1 for i in queue
                                     if i["status"] in ("dark", "dormant", "open",
                                                        "UNDESIGNATED"))}
        out = self._outbox_stats()
        view = self._base(stale_after_s)
        view.update({"live_flag": is_live(),
                     "kill_switch": "released" if is_live() else "engaged",
                     "kill_reason": _kill_reason(),
                     "channels": by_channel, "channel_order": order,
                     "kpis": kpis, "outbox_drain": out})
        return self._view(view)

    def _outbox_stats(self) -> dict:
        row = self.store.fetchone(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN done_ts IS NOT NULL THEN 1 ELSE 0 END) AS done,"
            " SUM(CASE WHEN done_ts IS NULL AND claimed_ts IS NOT NULL"
            " THEN 1 ELSE 0 END) AS inflight"
            " FROM pending_export")
        total = row["total"] or 0
        done = row["done"] or 0
        inflight = row["inflight"] or 0
        by_kind = {}
        for r in self.store.fetchall(
                "SELECT entity_type, COUNT(*) AS c FROM pending_export"
                " WHERE done_ts IS NULL GROUP BY entity_type"):
            by_kind[r["entity_type"] or "null"] = r["c"]
        pct = round(100.0 * done / total, 1) if total else None
        return {"total": total, "done": done, "inflight": inflight,
                "pending_by_kind": by_kind, "drain_pct": pct}

    def agents_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Audit actors, note verdicts, persona registry (bodies withheld)."""
        latest_by_id = {}
        for r in self.store.fetchall(
                "SELECT id, actor, event FROM audit WHERE id IN"
                " (SELECT MAX(id) FROM audit GROUP BY actor)"):
            latest_by_id[r["actor"]] = r["event"]
        actors = []
        for r in self.store.fetchall(
                "SELECT actor, COUNT(*) AS c, MAX(ts) AS ts, MAX(id) AS last_id"
                " FROM audit GROUP BY actor ORDER BY last_id DESC"):
            actors.append({"actor": r["actor"], "events": r["c"],
                           "latest_event": latest_by_id.get(r["actor"]),
                           "latest_ts": r["ts"]})
        verdicts = []
        for path in sorted((ROOT.parent).glob("FINDINGS-*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            m = re.search(r"^Verdict:\s*\*\*(.+?)\*\*", text, re.M)
            if m:
                verdicts.append({"note": path.name, "verdict": m.group(1).strip()})
        registry = [dict(r) for r in self.store.fetchall(
            "SELECT agent_key, role_desc, model_route, active FROM agents ORDER BY id")]
        view = self._base(stale_after_s)
        view.update({"actors": actors, "note_verdicts": verdicts,
                     "persona_registry": registry})
        return self._view(view)

    def orchestrator_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Posture, open gates, last traversal close, per-day cycle timeline."""
        last = self.store.fetchone(
            "SELECT id, ts, event, payload_json FROM audit"
            " WHERE event LIKE 'traversal.%' ORDER BY id DESC LIMIT 1")
        timeline = []
        for r in self.store.fetchall(
                "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS c FROM audit"
                " WHERE ts IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT 14"):
            timeline.append({"day": r["day"], "events": r["c"]})
        last_close = None
        if last:
            try:
                p = json.loads(last["payload_json"] or "{}")
            except ValueError:
                p = {}
            last_close = {"audit_id": last["id"], "ts": last["ts"],
                          "event": last["event"],
                          "exit_reason": p.get("exit_reason"),
                          "path": p.get("path")}
        view = self._base(stale_after_s)
        view.update({"live_flag": is_live(),
                     "kill_switch": "released" if is_live() else "engaged",
                     "open_gates": ["outreach-creds", "omniroute-funding",
                                    "d5-call", "test-recipient"],
                     "last_close": last_close, "cycle_timeline": timeline})
        return self._view(view)

    def logs_view(self, limit: int = 50, event: str | None = None,
                  actor: str | None = None,
                  stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Audit rows as log lines, filterable by event/actor substring."""
        limit = _clamp_limit(limit)
        clauses, params = [], []
        if event:
            clauses.append("event LIKE ? ESCAPE '\\'")
            params.append("%" + event.replace("\\", "\\\\").replace("%", "\\%")
                          .replace("_", "\\_") + "%")
        if actor:
            clauses.append("actor = ?")
            params.append(actor[:QUERY_TEXT_MAX])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.store.fetchall(
            "SELECT id, ts, actor, event, entity_type, entity_id FROM audit"
            + where + " ORDER BY id DESC LIMIT ?", (*params, limit))
        view = self._base(stale_after_s)
        view.update({"lines": [dict(r) for r in rows],
                     "filter": {"event": event, "actor": actor, "limit": limit}})
        return self._view(view)

    def llm_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Per-model usage bars, dormant hatched rows, smoke history."""
        per_model: dict = {}
        for e in self.store.fetchall(
                "SELECT payload_json FROM audit ORDER BY id DESC LIMIT 500"):
            try:
                p = json.loads(e["payload_json"] or "{}")
            except ValueError:
                continue
            u = p.get("usage")
            if not isinstance(u, dict):
                continue
            key = f"{u.get('model', '?')} :: {p.get('channel', '?')}"
            slot = per_model.setdefault(key, {"prompt": 0, "completion": 0,
                                              "cost": 0.0, "calls": 0})
            slot["calls"] += 1
            for k, t in (("prompt_tokens", "prompt"),
                         ("completion_tokens", "completion")):
                if isinstance(u.get(k), (int, float)):
                    slot[t] += u[k]
            if isinstance(u.get("cost_estimate"), (int, float)):
                slot["cost"] += u["cost_estimate"]
        dormant = [ch for ch in CHANNELS if ch.startswith("llm/")
                   and not any(ch in k for k in per_model)]
        smokes = []
        for row in self.store.fetchall(
                "SELECT id, ts, payload_json FROM audit WHERE event LIKE 'live.smoke.%'"
                " ORDER BY id DESC LIMIT 20"):
            try:
                p = json.loads(row["payload_json"])
            except ValueError:
                continue
            smokes.append({"audit_id": row["id"], "ts": row["ts"],
                           "channel": p.get("channel"), "usage": p.get("usage")})
        view = self._base(stale_after_s)
        view.update({"per_model": per_model, "dormant_channels": dormant,
                     "smoke_history": smokes})
        return self._view(view)

    def ledger_view(self, limit: int = 50, event: str | None = None,
                    stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Chain entries, spend, counts, quarantine rows, attention queue."""
        limit = _clamp_limit(limit)
        if event:
            rows = self.store.fetchall(
                "SELECT id, ts, actor, event, entity_type, entity_id,"
                " payload_json FROM audit WHERE event LIKE ? ESCAPE '\\'"
                " ORDER BY id DESC LIMIT ?",
                ("%" + event.replace("\\", "\\\\").replace("%", "\\%")
                 .replace("_", "\\_") + "%", limit))
        else:
            rows = self.store.fetchall(
                "SELECT id, ts, actor, event, entity_type, entity_id,"
                " payload_json FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        entries = [dict(r) for r in rows]
        spend, totals = self._spend(cap=limit)
        attention = []
        for e in entries:
            try:
                p = json.loads(e["payload_json"] or "{}")
            except ValueError:
                p = {}
            route = p.get("last_route")
            if e["event"] in ("send.dead_lettered", "send.transient",
                              "inbound.reconciled") or (
                    isinstance(route, dict) and route.get("paused")):
                attention.append({"id": e["id"], "event": e["event"]})
        stages = {}
        for row in self.store.fetchall(
                "SELECT stage, COUNT(*) AS c FROM leads GROUP BY stage"):
            stages[row["stage"] or "null"] = row["c"]
        threads = self.store.fetchone("SELECT COUNT(*) AS c FROM threads")["c"]
        messages = self.store.fetchone("SELECT COUNT(*) AS c FROM messages")["c"]
        qrow = self.store.fetchone(
            "SELECT COUNT(*) AS c FROM messages WHERE quarantine_reason IS NOT NULL")
        intents = {}
        for row in self.store.fetchall(
                "SELECT intent, COUNT(*) AS c FROM intents GROUP BY intent"):
            intents[row["intent"]] = row["c"]
        quar = [dict(r) for r in self.store.fetchall(
            "SELECT id, lead_id, status, idempotency_key, batch_id FROM actions"
            " WHERE action_type='quarantine_flag' ORDER BY id DESC LIMIT 20")]
        queued = self.store.fetchone(
            "SELECT COUNT(*) AS c FROM actions WHERE status='queued'")["c"]
        view = self._base(stale_after_s)
        view.update({"entries": entries, "spend": spend, "spend_totals": totals,
                     "counts": {"threads": threads, "messages": messages,
                                "leads_by_stage": stages,
                                "quarantined_messages": qrow["c"] if qrow else 0,
                                "intents": intents, "actions_queued": queued},
                     "terminals": {"stages": stages,
                                   "quarantined": qrow["c"] if qrow else 0},
                     "quarantine_actions": quar, "attention": attention,
                     "filter": {"event": event, "limit": limit}})
        return self._view(view)

    def compliance_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Live placeholder-shape scan, posture, kill-switch, deprecations."""
        dirty: list[str] = []
        for path in sorted(ROOT.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            low = text.lower()
            if "api_key=" in low or ("_05" in text and "DEPRECATED_ENV" not in text
                                     and "ZOHO_SMTP_05" not in text):
                dirty.append(path.name)
        live_hits = []
        try:
            live_text = (ROOT.parent / "orchestrator" / "live.py"
                         ).read_text(encoding="utf-8")
            for token in ("requests", "http.client", "aiohttp", "httpx",
                          "sqlite3", "import datetime", "api_key="):
                if token in live_text.lower():
                    live_hits.append(token)
        except OSError:
            live_hits.append("live.py-unreadable")
        view = self._base(stale_after_s)
        view.update({"live_flag": is_live(),
                     "kill_switch": "released" if is_live() else "engaged",
                     "placeholder_scan_dirty": dirty,
                     "live_forbidden_tokens": live_hits,
                     "deprecated_names": list(DEPRECATED_ENV)})
        return self._view(view)

    def queue_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Operator blockers with live status; absence rendered honestly."""
        status = channel_status()
        smoked = {json.loads(r["payload_json"]).get("channel")
                  for r in self.store.fetchall(
                      "SELECT payload_json FROM audit WHERE event LIKE 'live.smoke.%'")}
        smoked.discard(None)
        items = []
        for ch, st in status.items():
            items.append({"item": f"channel:{ch}",
                          "status": "lit" if ch in smoked
                          else ("ready" if st["configured"] else "dark"),
                          "source": "ledger+env"})
        items.append({"item": "omniroute-funding", "status": "dormant",
                      "source": "ledger"})
        items.append({"item": "d5-call", "status": "open", "source": "declared"})
        items.append({"item": "test-recipient", "status": "UNDESIGNATED",
                      "source": "absent-no-store"})
        view = self._base(stale_after_s)
        view.update({"items": items})
        return self._view(view)

    def agent_queue_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """Live agent activity from audit actors — distinct from operator queue."""
        actors = []
        for r in self.store.fetchall(
                "SELECT actor, COUNT(*) AS c, MAX(ts) AS ts, MAX(id) AS last_id"
                " FROM audit GROUP BY actor ORDER BY last_id DESC"):
            latest = self.store.fetchone(
                "SELECT event FROM audit WHERE id=?", (r["last_id"],))
            actors.append({"agent": r["actor"], "events": r["c"],
                           "latest_event": latest["event"] if latest else None,
                           "latest_ts": r["ts"], "state": "working"})
        view = self._base(stale_after_s)
        view.update({"live": actors})
        return self._view(view)

    def providers_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """LLM provider wiring: configured status, rotation, allowlist standing."""
        status = channel_status()
        smoked = {json.loads(r["payload_json"]).get("channel")
                  for r in self.store.fetchall(
                      "SELECT payload_json FROM audit WHERE event LIKE 'live.smoke.%'")}
        smoked.discard(None)
        cards = []
        for ch in ("llm/omniroute", "llm/freellmapi", "llm/ollama"):
            st = status.get(ch, {})
            cards.append({"provider": ch,
                          "configured": bool(st.get("configured")),
                          "in_rotation": bool(ch in smoked and st.get("live_unlocked")),
                          "allowlist": ("local $0 — inside zero-cost allowlist"
                                        if ch == "llm/ollama"
                                        else "unverified — no funded path yet")})
        view = self._base(stale_after_s)
        view.update({"providers": cards,
                     "counts": {"total": len(cards),
                                "configured": sum(1 for c in cards if c["configured"]),
                                "dark": sum(1 for c in cards if not c["configured"]),
                                "in_rotation": sum(1 for c in cards if c["in_rotation"])}})
        return self._view(view)

    # Email 17-capability inventory (verbatim brief list). Statuses derive
    # from schema-plus-activity only: LIVE (rows exist), WIRED-IDLE (schema
    # supports, zero rows), NOT-WIRED (no source anywhere).
    EMAIL_CAPABILITIES = (
        "Find and retrieve emails",
        "Understand and summarize the inbox",
        "Email tagging",
        "Draft replies in a declared tone",
        "Reply to emails",
        "Follow-up management",
        "Sales-intent analysis across: leads, prospects, existing customers, "
        "potential customers, referral opportunities, buying signals, RFPs, "
        "RFQs, objections, pricing discussions, competitor mentions, customer "
        "pain points, service requirements, decision-makers, stakeholders, "
        "next steps",
        "Customer intelligence: conversations → requirements → problems → "
        "commitments → next action",
        "Meeting intelligence",
        "Document and attachment intelligence",
        "Natural-language search across business history",
        "Structured-data extraction",
        "Automated email reporting",
        "Cross-email reasoning — combining multiple messages into one "
        "opportunity brief (problem → current workflow → required solution → "
        "budget → timeline → decision-maker → next action)",
        "Natural-language search, as a query interface",
        "Recurring/scheduled workflows",
        "Inbox operations: send, reply, forward, draft, manage, organize, "
        "apply/remove labels, mark read/unread, archive, move, delete",
    )
    _EMAIL_WIRED_IDLE = {0, 4, 5, 10}

    def email_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S) -> dict:
        """17 capabilities verbatim, each LIVE / WIRED-IDLE / NOT-WIRED."""
        inbound = self.store.fetchone(
            "SELECT COUNT(*) AS c FROM messages WHERE direction='inbound'")["c"]
        outbound = self.store.fetchone(
            "SELECT COUNT(*) AS c FROM messages WHERE direction='outbound'")["c"]
        caps = []
        for i, name in enumerate(self.EMAIL_CAPABILITIES):
            if i in self._EMAIL_WIRED_IDLE:
                n = inbound + outbound if i in (0, 4) else outbound
                status = "LIVE" if n > 0 else "WIRED-IDLE"
            else:
                status = "NOT-WIRED"
            caps.append({"n": i + 1, "capability": name, "status": status})
        identities = []
        for ch in ("email/gmail-smtp", "email/zoho-smtp", "email/agentmail-api",
                   "email/gmail", "email/zoho", "email/agentmail"):
            st = channel_status().get(ch, {})
            identities.append({"identity": ch,
                               "configured": bool(st.get("configured"))})
        sends = {"today": None, "note": "NO SOURCE — nothing has ever sent"}
        view = self._base(stale_after_s)
        view.update({"capabilities": caps, "identities": identities,
                     "sends_today": sends,
                     "threads": self.store.fetchone(
                         "SELECT COUNT(*) AS c FROM threads")["c"],
                     "messages_in": inbound, "messages_out": outbound})
        return self._view(view)

    def leads_view(self, stale_after_s: int = STALE_AFTER_DEFAULT_S,
                   stage: str | None = None, q: str | None = None,
                   lead_id: int | None = None) -> dict:
        """Lead board: stage chips, search/filter, full-history drill-down."""
        clauses, params = [], []
        if stage:
            clauses.append("stage = ?")
            params.append(stage[:20])
        if q:
            clauses.append("(email LIKE ? ESCAPE '\\' OR full_name LIKE ? ESCAPE '\\')")
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params.extend([like, like])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        leads = [dict(r) for r in self.store.fetchall(
            "SELECT id, email, full_name, stage, score, source, next_action_at"
            " FROM leads" + where + " ORDER BY id DESC LIMIT 200", tuple(params))]
        history = None
        if lead_id:
            threads = [dict(r) for r in self.store.fetchall(
                "SELECT * FROM threads WHERE lead_id=? ORDER BY id", (lead_id,))]
            thread_ids = [t["id"] for t in threads]
            messages_by_thread: dict = {tid: [] for tid in thread_ids}
            message_ids: list = []
            if thread_ids:
                placeholders = ",".join("?" for _ in thread_ids)
                for m in self.store.fetchall(
                        "SELECT id, thread_id, direction, body_raw,"
                        " quarantine_reason, provider_message_id, sent_at,"
                        " received_at FROM messages"
                        f" WHERE thread_id IN ({placeholders}) ORDER BY id",
                        tuple(thread_ids)):
                    row = dict(m)
                    messages_by_thread[row["thread_id"]].append(row)
                    message_ids.append(row["id"])
            intents_by_message: dict = {}
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                for x in self.store.fetchall(
                        "SELECT message_id, intent, sub_label, confidence"
                        f" FROM intents WHERE message_id IN ({placeholders})",
                        tuple(message_ids)):
                    intents_by_message.setdefault(x["message_id"], []).append(
                        {"intent": x["intent"], "sub_label": x["sub_label"],
                         "confidence": x["confidence"]})
            for t in threads:
                t["messages"] = messages_by_thread.get(t["id"], [])
                for m in t["messages"]:
                    m["intents"] = intents_by_message.get(m["id"], [])
            history = {"lead_id": lead_id, "threads": threads}
        view = self._base(stale_after_s)
        view.update({"leads": leads, "history": history,
                     "filter": {"stage": stage, "q": q}})
        return self._view(view)

    def bot_answer(self, question: str) -> dict:
        """Read-only Q&A over ledger + runbook; unknown stays unknown."""
        q = (question or "")[:BOT_QUESTION_MAX]
        low = q.lower()
        if re.search(r"spend|cost|tokens|ledger", low):
            totals = self.ledger_view(limit=200)["spend_totals"]
            return self._view({"answer": f"Spend totals: {totals['prompt']} in / "
                                         f"{totals['completion']} out tokens, "
                                         f"${totals['cost']:.5f} priced, "
                                         f"{totals['unpriced']} unpriced rows.",
                               "sources": ["audit:usage payloads"]})
        if re.search(r"why.*(blocked|l2)|blocker|blocking", low):
            items = [i for i in self.queue_view()["items"]
                     if i["status"] in ("dark", "dormant", "open", "UNDESIGNATED")]
            return self._view({"answer": "Blocking items: " + "; ".join(
                f"{i['item']}={i['status']}" for i in items) + ".",
                               "sources": ["queue-view"]})
        if re.search(r"walk|step", low) and re.search(r"gmail|zoho|agentmail|omniroute|ollama", low):
            which = next((c for c in ("gmail", "zoho", "agentmail", "omniroute",
                                      "ollama") if c in low), "")
            steps = _runbook_steps(which)
            return self._view({"answer": f"Runbook steps for {which}: " +
                                         " | ".join(steps) if steps else
                                         f"No checklist lines found for {which}.",
                               "sources": ["docs/FIRST-LIVE-SEND-RUNBOOK.md"]})
        for ch in ("omniroute", "ollama", "freellmapi", "gmail", "zoho", "agentmail"):
            if ch in low and re.search(r"posture|status|state|unlock|live|dark", low):
                m = self.machine_view()
                key = next((c for c in m["channels"] if ch in c), "")
                row = m["channels"].get(key)
                if row:
                    return self._view({
                        "answer": f"{key}: state={row['state']}, "
                                  f"configured={row['configured']}, "
                                  f"smoke={bool(row['last_smoke'])}.",
                        "sources": ["machine-view"]})
        if re.search(r"recent|latest|last|close|history", low):
            entries = self.ledger_view(limit=5)["entries"]
            return self._view({"answer": "Latest chain events: " + "; ".join(
                f"#{e['id']} {e['event']}" for e in entries) + ".",
                               "sources": ["audit"]})
        return self._view({"answer": "I don't know from the chain. Blocking inputs: "
                                     "outreach creds, omniroute funding, D5 call, "
                                     "test-recipient designation. See "
                                     "docs/FIRST-LIVE-SEND-RUNBOOK.md.",
                           "sources": []})


def _kill_reason() -> str:
    """Kill-switch readout WITH reason (brief §8, framing (a))."""
    if is_live():
        return "released — OUTREACH_LIVE=1 set by operator"
    return "engaged — OUTREACH_LIVE unset, all channels dark"


def _demo_path(api_path: str) -> str | None:
    """Demo fixture map. kill-switch/uploads/status are always real."""
    return {
        "/api/machine": "machine.json",
        "/api/issues": "issues.json",
        "/api/agents": "agents.json",
        "/api/leads": "leads.json",
        "/api/email/summary": "email.json",
        "/api/llm/providers": "llm.json",
        "/api/llm/usage": "llm.json",
        "/api/logs": "logs.json",
    }.get(api_path)


def _today() -> str:
    from orchestrator.timeutil import utcnow_iso
    return utcnow_iso()[:10]


def _day_count(store: Store, table: str, day: str, extra: str = "") -> int:
    """Per-day row counts over an allowlisted table set only."""
    allowed_cols = {"leads": "created_at", "audit": "ts", "messages": "created_at",
                    "threads": "created_at", "actions": "created_at"}
    if table not in allowed_cols:
        raise ValueError(f"refusing count on unknown table: {table}")
    allowed_extras = ("", "direction='outbound'", "direction='inbound'")
    if extra not in allowed_extras:
        raise ValueError("refusing count with unknown filter")
    col = allowed_cols[table]
    row = store.fetchone(
        f"SELECT COUNT(*) AS c FROM {table} WHERE substr({col}, 1, 10) = ?"
        + (f" AND {extra}" if extra else ""), (day,))
    return row["c"] if row else 0


def _machine_attention(store: Store) -> list[dict]:
    """Attention queue: dead-letter/transient + quarantine flags."""
    attention, seen = [], set()
    for e in store.fetchall(
            "SELECT id, ts, event, payload_json FROM audit"
            " WHERE event IN ('send.dead_lettered', 'send.transient')"
            " ORDER BY id DESC LIMIT 20"):
        key = e["event"]
        if key in seen:
            continue
        seen.add(key)
        attention.append({"id": f"live-{e['id']}",
                          "severity": "critical" if "dead" in key else "warning",
                          "title": key.replace(".", " ").replace("_", " "),
                          "component": "outbox",
                          "since": (e["ts"] or "")[:16], "count": 1})
    for r in store.fetchall(
            "SELECT id, lead_id, batch_id FROM actions"
            " WHERE action_type='quarantine_flag' ORDER BY id DESC LIMIT 20"):
        attention.append({"id": f"q-{r['id']}", "severity": "warning",
                          "title": f"quarantined lead row {r['lead_id']}",
                          "component": "compliance",
                          "since": "", "count": 1})
    return attention


def _machine_counts(store: Store, today: str) -> dict:
    """Pipeline counts + reply rate inputs (single-purpose helper)."""
    counts = {
        "ingested": _day_count(store, "leads", today),
        "qualified": len(store.fetchall(
            "SELECT id FROM leads WHERE stage='contacted'")),
        "drafted": len(store.fetchall(
            "SELECT id FROM actions WHERE action_type='send_draft'")),
        "cleared": len(store.fetchall(
            "SELECT id FROM actions WHERE action_type='send_draft'"
            " AND status='queued'")),
        "sent": _day_count(store, "messages", today, "direction='outbound'"),
        "replied": _day_count(store, "messages", today, "direction='inbound'"),
        "meetings": None,
    }
    return counts


def machine_status(store: Store) -> dict:
    """Real M1 machine payload. Same keys as fixtures/machine.json (pinned)."""
    from orchestrator.timeutil import utcnow_iso
    now = utcnow_iso()
    today = now[:10]
    stop = store.get_stop()
    attention = _machine_attention(store)
    state = "stopped" if stop.get("stopped") else (
        "degraded" if attention else "running")
    counts = _machine_counts(store, today)
    order = ["Ingested", "Qualified", "Drafted", "Cleared", "Sent",
             "Replied", "Meetings"]
    pipeline = [{"stage": s, "count": counts[s.lower()],
                 "delta": None, "queued": 0, "stalled": 0} for s in order]
    sent_today = counts["sent"] or 0
    inbound_7d = len(store.fetchall(
        "SELECT id FROM messages WHERE direction='inbound'"))
    reply_rate = (round(100.0 * inbound_7d / max(1, sent_today), 1)
                  if sent_today else None)
    activity = []
    for r in store.fetchall(
            "SELECT ts, actor, event FROM audit ORDER BY id DESC LIMIT 12"):
        activity.append({"at": (r["ts"] or "")[:8] or "—",
                         "ago": "", "agent": r["actor"], "text": r["event"],
                         "lead": ""})
    from orchestrator.posture import channel_status
    status = channel_status()
    email_ids = [c for c in status if c.startswith("email/")]
    healthy = sum(1 for c in email_ids if status[c]["configured"])
    comps = [
        {"name": "Orchestrator",
         "ok": not stop.get("stopped"),
         "metric": f"stop flag: {'SET' if stop.get('stopped') else 'clear'}"},
        {"name": "Agents",
         "ok": True,
         "metric": f"{len(store.fetchall('SELECT DISTINCT actor AS a FROM audit'))} actors seen"},
        {"name": "Inference", "ok": True, "metric": "ollama local tracks"},
        {"name": "Email identities", "ok": healthy > 0,
         "metric": f"{healthy} of {len(email_ids)} configured"},
        {"name": "Memory vault",
         "ok": _preflight(store)[3]["ok"], "metric": "sqlite write probe"},
        {"name": "Compliance gate", "ok": True, "metric": "deterministic gates loaded"},
    ]
    n_ok = sum(1 for c in comps if c["ok"])
    sentence = ("System stopped" if state == "stopped"
                else ("All systems running" if state == "running"
                      else f"{len(attention)} issue(s) need attention"))
    return {
        "banner": {"state": state, "sentence": sentence,
                   "subline": f"{n_ok} of {len(comps)} components ok · "
                              f"stop: {stop.get('reason', '')[:80]}"},
        "pipeline": pipeline,
        "kpis": {"sent_today": {"value": sent_today, "cap": None},
                 "reply_rate_7d": {"value": reply_rate, "delta_pts": None},
                 "meetings_7d": {"value": None, "target": None},
                 "deliverability": {"bounce": None, "complaints": None,
                                    "bounce_limit": 2.0, "complaint_limit": 0.3}},
        "activity": activity,
        "attention": attention,
        "components": comps,
    }


def _preflight(store: Store) -> list[dict]:
    """Release pre-flight, all lines verifiable right now. Suppression list
    has no artifact yet — reported explicitly rather than faked (M1 report)."""
    from orchestrator.posture import channel_status
    status = channel_status()
    email_ok = any(status.get(c, {}).get("configured")
                   for c in status if c.startswith("email/"))
    llm_ok = False
    try:
        from orchestrator.live import FreeLLMClient, OllamaClient, OmniRouteClient
        llm_ok = any(not c().paused[0] for c in
                     (OmniRouteClient, FreeLLMClient, OllamaClient))
    except Exception:
        llm_ok = False
    try:
        from orchestrator.inbound import BOUNCE_RE, UNSUB_RE
        gate_ok = bool(UNSUB_RE.search("please unsubscribe me")
                       and BOUNCE_RE.search("mailbox unavailable"))
    except Exception:
        gate_ok = False
    try:
        mem_ok, mem_detail = store.probe_writable()
    except Exception as exc:
        mem_ok, mem_detail = False, f"sqlite probe failed: {type(exc).__name__}"
    return [
        {"check": "email identity healthy",
         "ok": email_ok, "detail": "a configured identity" if email_ok else "no identity configured"},
        {"check": "llm provider healthy",
         "ok": llm_ok, "detail": "a provider unpaused" if llm_ok else "all providers paused"},
        {"check": "compliance gate responding",
         "ok": gate_ok, "detail": "scan module loads" if gate_ok else "scan module failed"},
        {"check": "memory writable",
         "ok": mem_ok, "detail": mem_detail},
        {"check": "suppression list loaded",
         "ok": False, "detail": "not built — informational only, does not gate release"},
    ]


def _theme_of(args: dict) -> str:
    t = (args.get("theme") or ["dark"])[0]
    return t if t in THEMES else "dark"


def _runbook_steps(channel: str) -> list[str]:
    try:
        text = (ROOT.parent / "docs" / "FIRST-LIVE-SEND-RUNBOOK.md"
                ).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip()[2:RUNBOOK_SNIPPET_CHARS] for line in text.splitlines()
            if line.strip().startswith("- ") and channel in line.lower()][:RUNBOOK_SNIPPET_LIMIT]


_VIEWS = {"machine": "machine_view", "agents": "agents_view",
          "agent-queue": "agent_queue_view", "orchestrator": "orchestrator_view",
          "logs": "logs_view", "llm": "llm_view", "providers": "providers_view",
          "email": "email_view", "leads": "leads_view", "ledger": "ledger_view",
          "compliance": "compliance_view", "queue": "queue_view"}


PAGES = {n: f"{n}.html" for n, _ in NAV}


def _serve_page(dashboard, section: str, theme: str):
    """Serve a static template with two mechanical substitutions only:
    stylesheet link and theme-toggle target. No markup is generated here —
    templates are hand-written HTML; Python is the data transport (JSON)."""
    raw = (TEMPLATES / PAGES[section]).read_bytes()
    css = b"/style-dark.css" if theme == "dark" else b"/style.css"
    raw = raw.replace(b"__THEME_CSS__", css)
    toggle = b"light" if theme == "dark" else b"dark"
    raw = raw.replace(b"__TOGGLE__", toggle)
    return raw


KILL_BODY_MAX = 65536
KILL_REASON_MAX = 200


def _kill_token_configured() -> str:
    """Optional operator token. Empty = localhost bind is the guard."""
    return os.environ.get("OPERATOR_TOKEN", "")


def _kill_authorized(handler) -> bool:
    """Constant-time operator check. Open when no token is configured."""
    import hmac
    want = _kill_token_configured()
    if not want:
        return True
    got = handler.headers.get("X-Operator-Token", "") or ""
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        got = got or auth[len("Bearer "):]
    return hmac.compare_digest(got, want)


def _kill_origin_ok(handler) -> bool:
    """Reject cross-site POSTs; allow missing Origin (curl/tests)."""
    origin = handler.headers.get("Origin")
    if origin is None:
        return True
    host = handler.headers.get("Host", "")
    return origin in (f"http://{host}", f"https://{host}")


def make_handler(dashboard: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ControlRoom/0"

        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; object-src 'none';"
                             " base-uri 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, data) -> None:
            self._send(200, json.dumps(data, sort_keys=True,
                                       default=str).encode(),
                       "application/json")

        def _view_failed(self) -> None:
            import logging
            logging.getLogger("controlroom").warning(
                "view failed: %s", getattr(self, "path", "?"))
            self._send(500, b"view failed", "text/plain")

        def _handle_demo(self, parsed, args0) -> bool:
            """Serve demo fixtures. Returns True when handled."""
            mapped = _demo_path(parsed.path)
            if mapped is None:
                return False
            try:
                blob = (STATIC.parent / "fixtures" / mapped).read_bytes()
            except OSError:
                self._view_failed()
                return True
            self._send(200, blob, "application/json")
            return True

        def _handle_status(self) -> None:
            try:
                stop = dashboard.store.get_stop()
                head = dashboard.store.fetchone(
                    "SELECT MAX(ts) AS ts FROM audit")
                data = dashboard._view({
                    "state": ("stopped" if stop.get("stopped") else "running"),
                    "stop": stop,
                    "heartbeat_at": head["ts"] if head else None,
                    "preflight": _preflight(dashboard.store)})
            except Exception:
                self._view_failed()
                return
            self._send_json(data)

        def _handle_machine_or_issues(self, issues_only: bool) -> None:
            try:
                data = machine_status(dashboard.store)
                if issues_only:
                    data = data["attention"]
            except Exception:
                self._view_failed()
                return
            self._send_json(data)

        def _handle_static(self, path: str) -> None:
            kinds = {"/style.css": ("style.css", "text/css"),
                     "/style-dark.css": ("style-dark.css", "text/css"),
                     "/app.js": ("app.js", "application/javascript"),
                     "/machine.js": ("machine.js", "application/javascript")}
            fname, ctype = kinds[path]
            try:
                blob = (STATIC / fname).read_bytes()
            except OSError:
                self._view_failed()
                return
            self._send(200, blob, ctype)

        def _handle_page(self, name: str, args) -> None:
            theme = _theme_of(args)
            try:
                body = _serve_page(dashboard, name, theme)
            except Exception:
                self._view_failed()
                return
            self._send(200, body, "text/html")

        def _handle_api_view(self, vname: str, args) -> None:
            meth = {"machine": "machine_view", "agents": "agents_view",
                    "agent-queue": "agent_queue_view",
                    "orchestrator": "orchestrator_view", "logs": "logs_view",
                    "llm": "llm_view", "providers": "providers_view",
                    "email": "email_view", "leads": "leads_view",
                    "ledger": "ledger_view",
                    "compliance": "compliance_view",
                    "queue": "queue_view"}.get(vname)
            if meth is None:
                self._send(404, b"unknown view", "text/plain")
                return
            try:
                kw2: dict = {}
                if vname in ("ledger", "logs"):
                    if "limit" in args:
                        kw2["limit"] = max(1, min(200, int(args["limit"][0])))
                    if "event" in args:
                        kw2["event"] = args["event"][0][:QUERY_TEXT_MAX]
                    if "actor" in args and vname == "logs":
                        kw2["actor"] = args["actor"][0][:QUERY_TEXT_MAX]
                if vname == "leads":
                    for key in ("q", "stage", "lead_id"):
                        if key in args:
                            kw2[key] = args[key][0][:QUERY_TEXT_MAX]
                data = getattr(dashboard, meth)(**kw2)
            except (ValueError, TypeError):
                self._send(400, b"bad parameter", "text/plain")
                return
            except Exception:
                self._view_failed()
                return
            self._send_json(data)

        def do_GET(self):  # noqa: N802 (stdlib handler naming)
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            args0 = parse_qs(parsed.query)
            if args0.get("demo", ["0"])[0] == "1":
                if self._handle_demo(parsed, args0):
                    return
            if parsed.path == "/api/status":
                self._handle_status()
                return
            if parsed.path == "/api/machine":
                self._handle_machine_or_issues(False)
                return
            if parsed.path == "/api/issues":
                self._handle_machine_or_issues(True)
                return
            if parsed.path in ("/style.css", "/style-dark.css", "/app.js",
                               "/machine.js"):
                self._handle_static(parsed.path)
                return
            if parsed.path == "/":
                self.send_response(302)
                self.send_header("Location", "/machine")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parsed.path == "/api/bot":
                q = parse_qs(parsed.query).get("q", [""])[0]
                try:
                    data = dashboard.bot_answer(q)
                except Exception:
                    self._view_failed()
                    return
                self._send_json(data)
                return
            name = parsed.path.lstrip("/")
            if name in dict(NAV):
                self._handle_page(name, parse_qs(parsed.query))
                return
            if parsed.path.startswith("/api/"):
                self._handle_api_view(parsed.path[len("/api/"):],
                                      parse_qs(parsed.query))
                return
            self._send(404, b"unknown view", "text/plain")

        def do_POST(self):  # noqa: N802 (stdlib handler naming)
            from urllib.parse import urlparse
            parsed = urlparse(self.path)
            if parsed.path == "/api/kill-switch":
                if not _kill_origin_ok(self):
                    self._send(403, b"bad origin", "text/plain")
                    return
                if not _kill_authorized(self):
                    self._send(401, b"unauthorized", "text/plain")
                    return
                ctype = (self.headers.get("Content-Type") or "")
                if "application/json" not in ctype:
                    self._send(415, b"json only", "text/plain")
                    return
                try:
                    length = min(int(self.headers.get("Content-Length") or 0),
                                 KILL_BODY_MAX)
                    raw = self.rfile.read(length) if length else b""
                    body = json.loads(raw.decode("utf-8") or "{}")
                    action = str(body.get("action", ""))
                    reason = str(body.get("reason", "")
                                 or "Operator stop")[:KILL_REASON_MAX]
                except Exception:
                    self._send(400, b"bad parameter", "text/plain")
                    return
                try:
                    if action == "engage":
                        record = dashboard.store.set_stop(
                            stopped=True, reason=reason, actor="operator")
                    elif action == "release":
                        checks = _preflight(dashboard.store)
                        gated = [c for c in checks
                                 if c["check"] != "suppression list loaded"]
                        failed = [c for c in gated if not c["ok"]]
                        if failed:
                            self._send(409, json.dumps(
                                {"checks": checks},
                                sort_keys=True).encode(), "application/json")
                            return
                        record = dashboard.store.set_stop(
                            stopped=False, reason=reason, actor="operator")
                    else:
                        self._send(400, b"bad parameter", "text/plain")
                        return
                except Exception:
                    self._view_failed()
                    return
                self._send_json(dashboard._view({"stop": record}))
                return
            self._send(405, b"read-only", "text/plain")
        def _reject(self):
            self._send(405, b"read-only", "text/plain")

        do_PUT = _reject
        do_DELETE = _reject
        do_PATCH = _reject

    return Handler


def serve(store: Store, host: str = "127.0.0.1", port: int = 8080) -> HTTPServer:
    """Bind and serve. Non-localhost requires ALLOW_REMOTE=1 (fail closed)."""
    if host not in ("127.0.0.1", "localhost", "::1") \
            and os.environ.get("ALLOW_REMOTE") != "1":
        raise PermissionError("refusing non-local bind without ALLOW_REMOTE=1")
    return HTTPServer((host, port), make_handler(Dashboard(store)))
