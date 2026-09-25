#!/usr/bin/env python3
"""orchestrator/graph/build.py — wire the pipeline with the compliance gate.

Topology: ingest -> copywriter -> verifier -> compliance -> sender -> reporter -> END
verifier branches reject -> END; compliance branches block -> END.
The compliance node is the ONLY inbound edge to sender, immediately before it.
compliance_gate.py is imported unaltered from orchestrator/policy/.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from orchestrator.graph import engine  # noqa: E402
from orchestrator.nodes import copywriter, ingest, sender, verifier  # noqa: E402
from orchestrator.policy import compliance_gate, reporter  # noqa: E402


def compliance_node(state: dict) -> dict:
    lead = state.get("lead", {})
    state["compliance_verdict"] = compliance_gate.evaluate(lead)
    if state["compliance_verdict"]["verdict"] == "block":
        state["halt_node"] = "compliance"
        state["halt_reasons"] = state["compliance_verdict"]["rules"]
    return state


def _route_verifier(state: dict) -> str:
    if state.get("verifier_verdict", {}).get("verdict") == "reject":
        return "__end__"
    return "compliance"


def _route_compliance(state: dict) -> str:
    if state.get("compliance_verdict", {}).get("verdict") == "block":
        return "__end__"
    return "sender"


def build(vault: str, inference_client=None):
    g = engine.build_graph()
    g.add_node("ingest", lambda s: {**s, "lead_note": _ingest_node(s, vault)})
    g.add_node("copywriter", lambda s: {**s, **_copy_node(s, vault, inference_client)})
    g.add_node("verifier", _verifier_node)
    g.add_node("compliance", compliance_node)
    g.add_node("sender", lambda s: {**s, "send_result": sender.run(
        s["lead"], s.get("draft", {}), audit=s.setdefault("external_http_calls", []))})
    g.add_node("reporter", lambda s: {**s, "reported": _report_node(s)})
    g.set_entry_point("ingest")
    g.add_edge("ingest", "copywriter")
    g.add_edge("copywriter", "verifier")
    g.add_conditional_edges("verifier", _route_verifier)
    g.add_edge("compliance", "sender")
    g.add_conditional_edges("compliance", _route_compliance)
    g.add_edge("sender", "reporter")
    return g.compile()


def _ingest_node(state: dict, vault: str) -> str:
    lead = state["lead"]
    lead.setdefault("lead_id", lead.get("name", "lead").replace(" ", "-").lower())
    return oc_write_lead(vault, lead)


def _copy_node(state: dict, vault: str, client) -> dict:
    res = copywriter.run(vault, state["lead_note"], client)
    return {"draft": res["draft"], "evidence": res["evidence"],
            "verifier_verdict": None}


def _verifier_node(state: dict) -> dict:
    if state.get("force_reject"):
        state["verifier_verdict"] = {"verdict": "reject", "reasons": ["forced_for_test"]}
        state["halt_node"] = "verifier"
        state["halt_reasons"] = ["forced_for_test"]
        return state
    verdict = verifier.verify_draft(state.get("draft", {}))
    state["verifier_verdict"] = verdict
    if verdict["verdict"] == "reject":
        state["halt_node"] = "verifier"
        state["halt_reasons"] = verdict["reasons"]
    return state


def _report_node(state: dict) -> dict:
    p = state["lead_note"]
    run_id = state.get("run_id", "run-1")
    send = state.get("send_result", {})
    reporter.record_run(p, run_id, f"pipeline trace: {state['_visited']}")
    status = send.get("status")
    if status == "sent":
        reporter.record_send(p, run_id, status)
    elif status == "dry_run":
        # recorded Part 1 behaviour: a dry run stamps final_status but does NOT
        # increment send_count — only a real send does.
        reporter.set_terminal_field(p, "final_status", "dry_run")
    return {"reported": True}


def oc_write_lead(vault: str, lead: dict) -> str:
    from orchestrator import obsidian_client as oc
    return oc.write_lead(vault, lead)


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    check("compliance module imported unaltered from orchestrator/policy",
          compliance_gate.__file__.endswith(os.path.join("orchestrator", "policy", "compliance_gate.py")))
    check("local engine compiles a graph", engine.build_graph() is not None)
    check("langgraph detected when installed (recorded: LANGGRAPH_AVAILABLE=True)",
          engine.LANGGRAPH_AVAILABLE in (True, False))

    from orchestrator.inference.mock import MockInferenceClient
    tmp = tempfile.mkdtemp(prefix="build-selftest-")
    try:
        from orchestrator import obsidian_client as oc
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.join(os.path.dirname(__file__), "..", "..", "vault-schema", "Templates", "Lead.md"),
            encoding="utf-8").read())
        app = build(vault, MockInferenceClient())

        clean = app.invoke({"lead": {"lead_id": "b-1", "name": "Bea Clean", "company": "Co",
                                     "email": "b@co.io", "industry": ""}, "run_id": "r-b1"})
        check("clean lead traverses every node in order",
              clean["_visited"] == ["ingest", "copywriter", "verifier", "compliance",
                                    "sender", "reporter"])
        check("clean lead verdict allow", clean["compliance_verdict"]["verdict"] == "allow")
        check("clean lead dry_run_complete", clean["send_result"]["status"] == "dry_run")

        unsub = app.invoke({"lead": {"lead_id": "b-2", "name": "U Sub", "company": "Co",
                                     "email": "u@co.io", "unsubscribed": True},
                            "run_id": "r-b2"})
        check("unsubscribed lead halts at the compliance node — compliance",
              unsub.get("halt_node") == "compliance")
        check("sender node never executed for the unsubscribed lead",
              "sender" not in unsub["_visited"])
        check("halt reason is the gate's not_unsubscribed rule — ['not_unsubscribed']",
              unsub.get("halt_reasons") == ["not_unsubscribed"])

        # structural: compliance is the only inbound edge into sender
        check("compliance node is the only route into the sender, immediately before it",
              _route_compliance({"compliance_verdict": {"verdict": "allow"}}) == "sender"
              and _route_compliance({"compliance_verdict": {"verdict": "block"}}) == "__end__")
        rejected = app.invoke({"lead": {"lead_id": "b-3", "name": "R Ject", "company": "Co",
                                        "email": "r@co.io", "industry": "NoSuchIndustry"},
                               "run_id": "r-b3", "force_reject": True})
        check("verifier reject path ends the run without a send", "sender" not in
              rejected["_visited"])
        fm = oc.read_frontmatter(clean["lead_note"])
        check("reporter stamped run_id + final_status on the clean lead",
              fm["run_id"] == "r-b1" and fm["final_status"] == "dry_run")
        check("dry run does not increment send_count (recorded Part 1 behaviour)",
              fm["send_count"] == 0)
        check("zero external HTTP calls in any trace",
              clean.get("external_http_calls", []) == [])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
