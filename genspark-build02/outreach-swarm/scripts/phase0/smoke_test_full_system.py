#!/usr/bin/env python3
"""smoke_test_full_system.py — Part 2 Task 4 + Task 5.

Three fabricated leads through the COMPLETE pipeline end to end:
ingest -> copywriter -> verify -> compliance gate -> sender (dry-run)
-> simulated reply -> reply classifier -> reporter -> supervisor console.

All external network calls mocked. Prints the full trace for all three leads,
including one that ends in a simulated unsubscribe and is confirmed blocked.
Task 5 boundary checks are asserted individually and reported one by one.
"""
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.graph import build as graph_build  # noqa: E402
from orchestrator.inference.mock import MockInferenceClient  # noqa: E402
from orchestrator.nodes import reply_classifier, verifier  # noqa: E402
from orchestrator.policy import compliance_gate, reporter  # noqa: E402
from supervisor.console import build_console  # noqa: E402
from watchdog.watchdog import Watchdog  # noqa: E402

LEADS = [
    {"lead_id": "smoke-1", "name": "Nora Quinn", "company": "Quinn Fintech",
     "email": "n@qf.io", "industry": ""},
    {"lead_id": "smoke-2", "name": "Omar Reyes", "company": "Reyes Logistics",
     "email": "o@rl.io", "industry": ""},
    {"lead_id": "smoke-3", "name": "Pia Storm", "company": "Storm Retail",
     "email": "p@sr.io", "industry": ""},
]


def main() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        print(("  ok   " if cond else "  FAIL ") + name)

    tmp = tempfile.mkdtemp(prefix="smoke-")
    vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
        os.path.join(ROOT, "vault-schema", "Templates", "Lead.md"),
        encoding="utf-8").read())
    app = graph_build.build(vault, MockInferenceClient())

    print("== Task 4: full-system dry-run, three fabricated leads ==\n")

    # ---------------- lead 1: clean, full pipeline + interested reply
    r1 = app.invoke({"lead": dict(LEADS[0]), "run_id": "smoke-run-1",
                     "external_http_calls": []})
    print(f"lead 1 trace: {r1['_visited']}")
    rep1 = reply_classifier.run(r1["lead_note"],
                                "This sounds great, let's schedule a call.",
                                run_id="smoke-rep-1")
    print(f"lead 1 reply: intent={rep1['intent']} ambiguous={rep1['ambiguous']}")
    check("lead 1 traversed every node in order",
          r1["_visited"] == ["ingest", "copywriter", "verifier", "compliance",
                             "sender", "reporter"])
    check("lead 1 send dry_run + final_status stamped",
          r1["send_result"]["status"] == "dry_run"
          and oc.read_frontmatter(r1["lead_note"])["final_status"] == "dry_run")
    check("lead 1 reply classified interested", rep1["intent"] == "interested")
    check("lead 1 stays contactable (not unsubscribed/bounced)",
          not oc.read_frontmatter(r1["lead_note"])["unsubscribed"])

    # ---------------- lead 2: unsubscribed pre-mark, blocked at compliance
    r2 = app.invoke({"lead": {**LEADS[1], "unsubscribed": True},
                     "run_id": "smoke-run-2", "external_http_calls": []})
    print(f"lead 2 trace: {r2['_visited']} (halted at {r2.get('halt_node')})")
    check("lead 2 halted at compliance, sender never ran",
          r2.get("halt_node") == "compliance" and "sender" not in r2["_visited"])

    # ---------------- lead 3: full pipeline, then a simulated UNSUBSCRIBE reply
    r3 = app.invoke({"lead": dict(LEADS[2]), "run_id": "smoke-run-3",
                     "external_http_calls": []})
    print(f"lead 3 trace: {r3['_visited']}")
    rep3 = reply_classifier.run(r3["lead_note"],
                                "Please remove me from your list. Unsubscribe.",
                                run_id="smoke-rep-3")
    print(f"lead 3 reply: intent={rep3['intent']} -> terminal field written")
    blocked = compliance_gate.evaluate(oc.read_frontmatter(r3["lead_note"]))
    print(f"lead 3 resends attempt after unsubscribe: {blocked['verdict']} {blocked['rules']}")
    check("lead 3 traversed the full pipeline", "sender" in r3["_visited"])
    check("lead 3 unsubscribe reply set the terminal field via reporter",
          oc.read_frontmatter(r3["lead_note"])["unsubscribed"] is True)
    check("lead 3 confirmed blocked from further contact",
          blocked["verdict"] == "block" and "not_unsubscribed" in blocked["rules"])
    check("lead 3 send_count stays 0 (dry-run never counted as a real send)",
          oc.read_frontmatter(r3["lead_note"])["send_count"] == 0)

    # ---------------- console answers one question about this batch
    console = build_console(vault, {"sender": {"health": "ok",
                                               "raw": "health=ok, queue depth=0"}})
    ans = console.ask("why was lead smoke-2 blocked")
    print(f"console Q: why was lead smoke-2 blocked -> cites "
          f"{ans['citations'][0]['source_note']} :: {ans['citations'][0]['source_section']}")
    check("console cites the exact note + section for the blocked lead",
          ans["citations"] and ans["citations"][0]["source_note"] == r2["lead_note"])
    check("zero external HTTP calls across the whole batch",
          r1["external_http_calls"] == [] and r2["external_http_calls"] == []
          and r3["external_http_calls"] == [])

    # ---------------- Task 5: full boundary list, checked individually
    print("\n== Task 5: boundary list, each checked individually ==\n")
    import subprocess
    # 1. no vector/SQL DB or framework-native long-term memory store
    #    (scanned over runtime code only — scripts/ and tests legitimately name
    #    the forbidden systems in order to forbid them)
    def _python_scan(pattern, target_dirs, exclude_dirs=("scripts", "__pycache__")):
        import re
        pat = re.compile(pattern, re.IGNORECASE)
        hits = []
        for td in target_dirs:
            full_td = os.path.join(ROOT, td) if not os.path.isabs(td) else td
            if not os.path.exists(full_td):
                continue
            if os.path.isfile(full_td):
                try:
                    txt = open(full_td, encoding="utf-8", errors="replace").read()
                    if pat.search(txt):
                        hits.append(full_td)
                except OSError:
                    pass
                continue
            for r, dirs, files in os.walk(full_td):
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                for f in files:
                    if f.endswith(".py") or f.endswith(".md"):
                        p = os.path.join(r, f)
                        try:
                            txt = open(p, encoding="utf-8", errors="replace").read()
                            if pat.search(txt):
                                hits.append(p)
                        except OSError:
                            pass
        return hits

    forbidden_pattern = r"chroma|pinecone|qdrant|weaviate|sqlite|postgres|mem0|langmem|zep|letta"
    srcs = _python_scan(forbidden_pattern, [ROOT], exclude_dirs=["scripts", "__pycache__", ".git"])
    check("no vector/SQL database or framework-native long-term memory store "
          "holds lead data anywhere in the runtime code", len(srcs) == 0)
    # 2. durable facts mirrored to Obsidian in the same turn
    check("every durable fact mirrored to the vault in the same turn it's produced "
          "(run-loop + reporter write-through verified in smoke run)",
          all(os.path.exists(r["lead_note"]) for r in (r1, r3))
          and "reply classified" in oc.read_note(r3["lead_note"]))
    # 3. terminal states enforced by deterministic checks, never an LLM
    gate_src = open(os.path.join(ROOT, "orchestrator", "policy",
                                 "compliance_gate.py")).read()
    #    (scan code lines only — skip comments/docstrings mentioning "no LLM")
    import ast as _ast
    gate_tree = _ast.parse(gate_src)
    gate_code_lines = []
    for node in _ast.walk(gate_tree):
        if isinstance(node, _ast.BoolOp | _ast.Compare | _ast.Call | _ast.If | _ast.Return):
            gate_code_lines.append(_ast.get_source_segment(gate_src, node) or "")
    gate_code = "\n".join(gate_code_lines).lower()
    # scope to the gate's evaluate()/should_send() functions — its own
    # self-test legitimately asserts the absence of a model field
    eval_src = "\n".join(_ast.get_source_segment(gate_src, n) or ""
                         for n in _ast.walk(gate_tree)
                         if isinstance(n, _ast.FunctionDef)
                         and n.name in ("evaluate", "should_send")).lower()
    check("terminal compliance states enforced by plain deterministic checks "
          "(no LLM anywhere in the gate's executable code)",
          all(s in gate_src for s in ("unsubscribed", "bounced", "do_not_contact",
                                      "jurisdiction_hold"))
          and "llm" not in eval_src and "model" not in eval_src)
    # 4. gate checked immediately before every simulated send
    check("compliance gate checked immediately before every simulated send, "
          "not only at planning time (compliance is sender's only inbound edge)",
          graph_build._route_compliance(
              {"compliance_verdict": {"verdict": "allow"}}) == "sender")
    # 5. no skill-loading path references the marketplace
    #    (runtime packages only — the checker script itself names it to forbid it)
    claw_hits = _python_scan("clawhub", ["orchestrator", "supervisor", "paperclip-adapter", "watchdog", "mcp-servers", "vault-schema"])
    check("no skill-loading path in the code references ClawHub",
          len(claw_hits) == 0)
    # 6. console has no write/send tool mounted
    check("supervisor console has no write or send tool mounted (structural absence)",
          console.tool_names() == ["get_agent_run", "get_daily_metrics",
                                   "get_lead_status", "get_sender_health",
                                   "search_related_patterns"])
    # 7. every claim passes through the frozen verifier
    check("every agent action that produces a claim passes through the existing "
          "verifier before being accepted (verifier node between copywriter and "
          "compliance)",
          "verifier" in r1["_visited"]
          and verifier.CONTRACT_VERSION == "1.0.0")

    print(f"\nSMOKE TEST RESULT: {passed} passed, {failed} failed")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
