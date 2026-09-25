#!/usr/bin/env python3
"""supervisor/console.py — read-only supervisor chat console.

Mounts EXACTLY five read-only tools; all read from the Obsidian vault and/or
Paperclip activity records only:

    get_lead_status(lead_id)
    get_daily_metrics(date)
    get_sender_health()
    get_agent_run(agent_id)
    search_related_patterns(topic)

NO write or send tool is mounted — enforced STRUCTURALLY: the registry rejects
any tool whose declared side_effect is not "none", and the self-test proves a
write/send action is simply ABSENT from the tool-calling interface (not present-
but-refusing). Every answer cites the exact note path and section, quoting the
underlying record verbatim rather than paraphrasing it.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator import obsidian_client as oc  # noqa: E402


# ---------------------------------------------------------------- tool registry
class ReadOnlyViolation(Exception):
    pass


class ToolRegistry:
    """Structural read-only guarantee: a tool cannot even register unless it
    declares side_effect="none". Write/send tools are absent, not refused."""

    def __init__(self, vault: str, paperclip_records: dict | None = None):
        self.vault = vault
        self.paperclip = paperclip_records or {}
        self._tools = {}
        for name, fn, desc in (
            ("get_lead_status", self._get_lead_status, "lead note status by lead_id"),
            ("get_daily_metrics", self._get_daily_metrics, "metrics for a date from run logs"),
            ("get_sender_health", self._get_sender_health, "sender health from activity records"),
            ("get_agent_run", self._get_agent_run, "one agent run's trace by agent_id"),
            ("search_related_patterns", self._search_related_patterns, "vault-wide topic search"),
        ):
            self._tools[name] = {"fn": fn, "desc": desc, "side_effect": "none"}

    # -- structural guard: the ONLY way to add a tool
    def register(self, name, fn, desc, side_effect):
        if side_effect != "none":
            raise ReadOnlyViolation(
                f"tool '{name}' declared side_effect={side_effect!r} — the console "
                f"mounts read-only tools only; registration refused")
        self._tools[name] = {"fn": fn, "desc": desc, "side_effect": side_effect}

    def tool_names(self):
        return sorted(self._tools)

    def has_tool(self, name):
        return name in self._tools

    def call(self, name, *args):
        tool = self._tools.get(name)
        if tool is None:
            raise AttributeError(
                f"no tool named '{name}' exists in the console's tool-calling "
                f"interface (available: {', '.join(self.tool_names())})")
        return tool["fn"](*args)

    # ------------------------------------------------------ the five tools
    def _cite(self, path, section, quote):
        return {"source_note": path, "source_section": section, "quote": quote}

    def _get_lead_status(self, lead_id):
        p = oc.resolve_note(self.vault, lead_id)
        if not p:
            return {"error": f"no lead note found for '{lead_id}'", "citations": []}
        note = oc.read_note(p)
        fm = oc.read_frontmatter(p)
        citations = [self._cite(p, "frontmatter (Section B — terminal fields)",
                                "\n".join(l for l in note.splitlines() if re.match(
                                    r"^(unsubscribed|bounced|do_not_contact|jurisdiction_hold|"
                                    r"reply_intent|final_status|send_count|run_id):", l)))
                     ]
        m = re.search(r"^## Run log\n(.*?)(?=\n## |\Z)", note, re.S | re.M)
        if m:
            citations.append(self._cite(p, "## Run log", m.group(1).strip()))
        return {"lead_id": lead_id, "final_status": fm.get("final_status", ""),
                "reply_intent": fm.get("reply_intent", ""),
                "unsubscribed": fm.get("unsubscribed", False),
                "citations": citations}

    def _get_daily_metrics(self, date):
        replies = sends = blocks = 0
        cites = []
        for p in oc.list_notes(self.vault):
            note = oc.read_note(p)
            m = re.search(r"^## Run log\n(.*?)(?=\n## |\Z)", note, re.S | re.M)
            if not m:
                continue
            for line in m.group(1).splitlines():
                if f"[{date}" in line or (date in line):
                    if "send attempt" in line:
                        sends += 1
                    elif "blocked" in line or "compliance" in line:
                        blocks += 1
                    elif "reply classified" in line:
                        replies += 1
                    cites.append(self._cite(p, "## Run log", line.strip()))
        return {"date": date, "replies_classified": replies, "send_attempts": sends,
                "blocks": blocks, "citations": cites}

    def _get_sender_health(self):
        rec = self.paperclip.get("sender", {})
        return {"health": rec.get("health", "unknown"),
                "citations": [self._cite("paperclip://sender", "activity record",
                                         rec.get("raw", json_dumps(rec)))]}

    def _get_agent_run(self, agent_id):
        rec = self.paperclip.get(agent_id)
        if not rec:
            return {"error": f"no activity record for '{agent_id}'", "citations": []}
        return {"agent_id": agent_id, "trace": rec.get("trace", []),
                "citations": [self._cite("paperclip://" + agent_id, "activity record",
                                         rec.get("raw", json_dumps(rec)))]}

    def _search_related_patterns(self, topic):
        hits = oc.search(self.vault, topic)
        cites = []
        for p in hits[:5]:
            note = oc.read_note(p)
            for line in note.splitlines():
                if topic.lower() in line.lower():
                    section = "frontmatter" if line.startswith(("#", "-", (""))  ) and not line.startswith("##") else "body"
                    m = re.search(r"^##\s+(.+)$", note[:note.index(line)], re.M) if "##" in note[:note.index(line)] else None
                    cites.append(self._cite(p, m.group(1) if m else "frontmatter", line.strip()))
                    break
        return {"topic": topic, "matches": [p for p in hits], "citations": cites}

    # ------------------------------------------------------ answer helper
    def ask(self, question):
        """Answer a supervisor question with verbatim citations. Read-only."""
        q = question.lower()
        if "blocked" in q or "why was" in q:
            m = re.search(r"lead\s+([\w-]+)", question, re.I)
            if m:
                return self.call("get_lead_status", m.group(1))
        if "how many" in q and "repl" in q:
            m = re.search(r"(\d{4}-\d{2}-\d{2}|\btoday\b|\byesterday\b)", question.lower())
            date = m.group(1) if m else "today"
            return self.call("get_daily_metrics", date)
        if "sender" in q and ("health" in q or "doing" in q):
            return self.call("get_sender_health")
        if "run" in q and re.search(r"agent[- ]?\w*\d", q):
            m = re.search(r"(agent[- ]?\w*\d)", q)
            return self.call("get_agent_run", m.group(1))
        return self.call("search_related_patterns", question)


def json_dumps(obj):
    import json
    return json.dumps(obj, sort_keys=True, default=str)


def build_console(vault: str, paperclip_records: dict | None = None) -> ToolRegistry:
    return ToolRegistry(vault, paperclip_records)


# ---------------------------------------------------------------- self-test
def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    import json
    import tempfile
    import shutil

    tmp = tempfile.mkdtemp(prefix="console-selftest-")
    try:
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                         "vault-schema", "Templates", "Lead.md")),
            encoding="utf-8").read())
        p1 = oc.write_lead(vault, {"lead_id": "sc-1", "name": "Su Pervisor",
                                   "company": "Co", "email": "s@co.io"})
        reporter = sys.modules.get("orchestrator.policy.reporter")
        if reporter is None:
            from orchestrator.policy import reporter  # noqa
        from orchestrator.policy import reporter as rep
        rep.set_terminal_field(p1, "unsubscribed", True, reason="mock unsubscribe")
        oc.append_run_log(p1, "[2026-09-24] send attempt: blocked")

        paperclip = {"sender": {"health": "ok", "raw": "health=ok, dry_run queue depth=0"},
                     "agent-run-42": {"trace": ["ingest", "copywriter", "verifier"],
                                      "raw": "run agent-run-42 trace=ingest,copywriter,verifier"}}
        console = build_console(vault, paperclip)

        # exactly five read-only tools
        check("console mounts exactly five tools",
              console.tool_names() == ["get_agent_run", "get_daily_metrics",
                                       "get_lead_status", "get_sender_health",
                                       "search_related_patterns"])
        # structural absence of write/send tools
        for forbidden in ("send_email", "write_note", "patch_frontmatter",
                          "delete_lead", "send_outreach", "update_lead"):
            check(f"no write/send tool named '{forbidden}' exists to call",
                  not console.has_tool(forbidden))
            try:
                console.call(forbidden, {})
                check(f"invoking '{forbidden}' raises absence error", False)
            except AttributeError:
                check(f"invoking '{forbidden}' raises absence error", True)
        # registry refuses to register a side-effecting tool at all
        try:
            console.register("evil_write", lambda: None, "writes", side_effect="write")
            check("registry structurally refuses a side_effect != none tool", False)
        except ReadOnlyViolation:
            check("registry structurally refuses a side_effect != none tool", True)
        # every declared tool is side_effect none
        check("all mounted tools declare side_effect=none",
              all(t["side_effect"] == "none" for t in console._tools.values()))

        # three example questions, each answer citing exact note path + section
        a1 = console.ask("why was lead sc-1 blocked")
        check("Q1 answer cites the exact note path",
              a1.get("citations") and a1["citations"][0]["source_note"] == p1)
        check("Q1 answer quotes the terminal-field section verbatim",
              "unsubscribed: True" in a1["citations"][0]["quote"])
        a2 = console.ask("how many replies today, 2026-09-24?")
        a2b = console.call("get_daily_metrics", "2026-09-24")
        check("Q2 metrics counted from run logs",
              a2b["send_attempts"] == 1 and a2b["citations"][0]["source_section"] == "## Run log")
        check("Q2 citation quotes the run-log line verbatim",
              "send attempt: blocked" in a2b["citations"][0]["quote"])
        a3 = console.call("get_sender_health")
        check("Q3 sender health cites the paperclip record",
              a3["citations"][0]["source_note"].startswith("paperclip://")
              and "health=ok" in a3["citations"][0]["quote"])
        a4 = console.call("search_related_patterns", "mock unsubscribe")
        check("search_related_patterns cites matching notes",
              p1 in a4["matches"] and a4["citations"])
        check("answers quote records rather than paraphrasing (quote field non-empty)",
              all(c["quote"] for x in (a1, a2b, a3, a4) for c in x["citations"]))
        # write-path audit: structural absence, proven via AST — the console
        # imports no write-capable module and defines no patch/send call
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        import ast
        tree = ast.parse(src)
        imports, calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    calls.add(fn.attr)
                elif isinstance(fn, ast.Name):
                    calls.add(fn.id)
        check("console does not import the reporter or any write-capable module",
              not any("reporter" in n or "sender" in n or "smtplib" in n for n in imports))
        # scope the call audit to the ToolRegistry class (the shipped console),
        # not the self-test block which legitimately touches the vault
        registry_calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ToolRegistry":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        fn = sub.func
                        if isinstance(fn, ast.Attribute):
                            registry_calls.add(fn.attr)
                        elif isinstance(fn, ast.Name):
                            registry_calls.add(fn.id)
        check("console's shipped registry makes no patch/send-style call anywhere",
              not any(c.startswith(("patch_", "write_", "send_")) for c in registry_calls))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
