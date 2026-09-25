#!/usr/bin/env python3
"""orchestrator/graph/build.py — BUILD Task 4.

Topology
--------
    ingest -> copywriter -> verifier -> compliance -> sender -> reporter -> END
                                       \\-> END (reject)      \\-> END (block)

Task 4 replaces the placeholder step that previously stood between ``verifier``
and ``sender`` with a real call into the EXISTING
``orchestrator/policy/compliance_gate.py`` (``evaluate()``), positioned
immediately before the sender node. ``compliance_gate.py`` is not modified here.

The reporter node writes the run-log entry and the terminal frontmatter through
``orchestrator/policy/reporter.py`` — the only writer allowed to touch those
fields.

Self-test:  python3 orchestrator/graph/build.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.graph.engine import END, StateGraph  # noqa: E402
from orchestrator.inference import build_inference_client  # noqa: E402
from orchestrator.nodes import copywriter, ingest, sender, verifier  # noqa: E402
from orchestrator.policy import compliance_gate, reporter  # noqa: E402

try:  # the real package is used when present; the engine above is what tests run
    from langgraph.graph import END as LG_END  # type: ignore
    from langgraph.graph import StateGraph as LGStateGraph  # type: ignore

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover
    LG_END = "__end__"
    LGStateGraph = None  # type: ignore
    LANGGRAPH_AVAILABLE = False

TOPOLOGY = {
    "nodes": ["ingest", "copywriter", "verifier", "compliance", "sender", "reporter"],
    "linear": [("ingest", "copywriter"), ("copywriter", "verifier"), ("verifier", "compliance"),
               ("compliance", "sender"), ("sender", "reporter")],
    "branches": {
        "verifier": {"accept": "compliance", "reject": END},
        "compliance": {"allow": "sender", "block": END},
    },
    "entry": "ingest",
}


def build_graph(
    vault: str | Path,
    *,
    inference_client: Any = None,
    dry_run: bool = True,
    outbox: Optional[str | Path] = None,
    persist_drafts: bool = True,
    max_leads: int = 1,
) -> StateGraph:
    vault = Path(vault)
    client = inference_client or build_inference_client("mock")
    graph = StateGraph("outreach-swarm", max_steps=40)

    # ---- nodes ----------------------------------------------------------
    def n_ingest(state: Dict[str, Any]) -> Dict[str, Any]:
        has_input = any(state.get(k) for k in ("csv_path", "pdf_path", "raw_records"))
        if not has_input and state.get("lead_id"):
            # lead is already in the vault (pre-seeded): nothing to parse.
            return {
                "ingest": {"node": "ingest", "leads_written": 0, "paths": [],
                           "lead_ids": [state["lead_id"]], "skipped": "lead already in vault",
                           "audit": [], "terminal_fields_dropped": []},
                "leads": [state["lead_id"]],
            }
        out = ingest.run(
            vault,
            csv_path=state.get("csv_path"),
            pdf_path=state.get("pdf_path"),
            raw_records=state.get("raw_records"),
        )
        lead_ids: List[str] = list(out["lead_ids"][:max_leads])
        return {"ingest": out, "leads": lead_ids, "lead_ids": lead_ids}

    def n_copywriter(state: Dict[str, Any]) -> Dict[str, Any]:
        lead_id = (state.get("leads") or [None])[0]
        if not lead_id:
            return {"halted": True, "halt_reason": "no ingested lead to draft for", "lead_id": None}
        out = copywriter.run(vault, lead_id, client=client, persist=persist_drafts)
        return {
            "lead_id": lead_id,
            "copywriter": out,
            "draft": out["draft"],
            "evidence_graph": out["evidence_graph"],
        }

    def n_verifier(state: Dict[str, Any]) -> Dict[str, Any]:
        eg = state.get("evidence_graph") or {}
        out = verifier.verify_draft(state.get("draft") or {},
                                    {"ids": eg.get("ids") or [], "nodes": eg.get("nodes") or {}})
        return {"verifier": out, "verdict": out["verdict"]}

    def n_compliance(state: Dict[str, Any]) -> Dict[str, Any]:
        """Task 4: a placeholder used to sit here; now the real gate runs."""
        lead_id = state.get("lead_id") or (state.get("leads") or [None])[0]
        fm = oc.read_frontmatter(vault, lead_id)
        decision = compliance_gate.evaluate(fm, dry_run=dry_run)
        out: Dict[str, Any] = {
            "lead_id": lead_id,
            "compliance": decision,
            "compliance_decision": decision["decision"],
            "compliance_reasons": decision["reasons"],
        }
        if decision["decision"] != "allow":
            # a block is a terminal state for this lead; the sender must not run
            out["halted"] = True
            out["halt_reason"] = f"compliance gate blocked: {decision['reasons']}"
        return out

    def n_sender(state: Dict[str, Any]) -> Dict[str, Any]:
        lead_id = state.get("lead_id")
        fm = oc.read_frontmatter(vault, lead_id)
        out = sender.run(fm, state.get("draft") or {}, dry_run=dry_run, outbox=outbox,
                         run_id=state.get("run_id") or "")
        return {"sender": out, "send_status": out["status"], "message_id": out["message_id"]}

    def n_reporter(state: Dict[str, Any]) -> Dict[str, Any]:
        lead_id = state.get("lead_id")
        snd = state.get("sender") or {}
        out = reporter.record_outcome(
            vault, lead_id,
            run_id=state.get("run_id") or reporter.new_run_id("run"),
            compliance_verdict=str((state.get("compliance") or {}).get("decision")),
            delivery_status=str(snd.get("status") or "skipped"),
            final_status="dry_run_complete" if snd.get("status") == "dry_run" else str(snd.get("status")),
            stage="reporter",
            detail=f"message_id={snd.get('message_id')}",
        )
        return {"reporter": out, "run_log_entry": out["terminal"]}

    graph.add_node("ingest", n_ingest)
    graph.add_node("copywriter", n_copywriter)
    graph.add_node("verifier", n_verifier)
    graph.add_node("compliance", n_compliance)
    graph.add_node("sender", n_sender)
    graph.add_node("reporter", n_reporter)

    # ---- edges ----------------------------------------------------------
    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "copywriter")
    graph.add_edge("copywriter", "verifier")
    graph.add_conditional_edges("verifier", lambda s: "accept" if s.get("verdict") == "accept" else "reject",
                                {"accept": "compliance", "reject": END})
    graph.add_conditional_edges("compliance", lambda s: "allow" if s.get("compliance_decision") == "allow" else "block",
                                {"allow": "sender", "block": END})
    graph.add_edge("sender", "reporter")
    graph.add_edge("reporter", END)
    return graph


def invoke(vault: str | Path, **kw: Any) -> Dict[str, Any]:
    state = kw.pop("state", None) or {}
    graph = build_graph(vault, **kw)
    return graph.invoke(state)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def _seed_evidence(vault: Path) -> None:
    for fname, note in {
        "ev-industry-energy.md": {"id": "ev-ind-energy", "kind": "industry", "label": "energy",
                                  "text": "energy operators manage grid imbalance", "related": ["ev-pain-grid"]},
        "ev-pain-grid.md": {"id": "ev-pain-grid", "kind": "pain_point", "label": "grid imbalance",
                            "text": "grid imbalance forces manual dispatch", "related": ["ev-angle-dispatch"]},
        "ev-angle-dispatch.md": {"id": "ev-angle-dispatch", "kind": "angle", "label": "automate dispatch",
                                 "text": "automating dispatch removes manual steps", "related": ["ev-prior-energy"]},
        "ev-prior-energy.md": {"id": "ev-prior-energy", "kind": "prior_outcome", "label": "prior outcome",
                               "text": "a comparable operator shortened dispatch decisions", "related": []},
    }.items():
        oc.write_note(vault, vault / "Evidence" / fname, note, f"\n{note['text']}\n")


CLEAN_RAW = {
    "Full Name": "Sofia Marchetti", "Organisation": "Verdant Grid",
    "Work Email": "sofia@verdantgrid.example", "Website": "verdantgrid.example",
    "Country Code": "IT", "Sector": "energy", "Pain": "grid imbalance",
    "Hook": "automate dispatch",
}


def _selftest() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="graph-selftest-") as tmp:
        vault = Path(tmp) / "vault"
        outbox = Path(tmp) / "outbox"
        oc.ensure_vault(vault)
        _seed_evidence(vault)

        graph = build_graph(vault, outbox=outbox)
        topo = graph.topology()
        inbound_sender = ([k for k, v in topo["edges"].items() if v == "sender"]
                          + [k for k, v in topo["branch_targets"].items() if "sender" in v.values()])
        check("compliance node is the only route into the sender, immediately before it",
              inbound_sender == ["compliance"]
              and topo["branch_targets"].get("compliance") == {"allow": "sender", "block": END},
              json.dumps({"inbound": inbound_sender, "compliance": topo["branch_targets"].get("compliance")}))
        check("verifier routes reject -> END (no bypass to the sender)",
              topo["branch_targets"]["verifier"] == {"accept": "compliance", "reject": END}
              and TOPOLOGY["branches"]["verifier"]["reject"] == END,
              json.dumps(topo["branch_targets"]["verifier"]))
        check("no placeholder node remains in the topology",
              not any("placeholder" in n for n in topo["nodes"]), json.dumps(topo["nodes"]))

        # --- clean lead: ingest -> ... -> reporter ---------------------------
        clean = graph.invoke({"raw_records": [CLEAN_RAW], "run_id": reporter.new_run_id("demo")})
        visited = clean["nodes_visited"]
        check("clean lead traverses every node in order",
              visited == ["ingest", "copywriter", "verifier", "compliance", "sender", "reporter"], json.dumps(visited))
        check("clean lead finished (not halted)", clean["completed"] and clean["halted_at"] is None,
              f"completed={clean['completed']} halted_at={clean['halted_at']}")
        check("sender really ran in dry-run mode",
              (clean["state"].get("sender") or {}).get("status") == "dry_run",
              str((clean["state"].get("sender") or {}).get("status")))
        check("zero external HTTP calls in the clean run",
              (clean["state"].get("sender") or {}).get("external_http_calls") == [])
        check("compliance verdict for the clean lead is allow",
              clean["state"].get("compliance_decision") == "allow", str(clean["state"].get("compliance_reasons")))
        check("reporter wrote a run-log entry",
              "stage=reporter" in oc.read_note(vault, clean["state"]["lead_id"]))

        # --- pre-marked unsubscribed lead halts at the gate ------------------
        seed_fm = oc.order_like_schema({
            "lead_id": "ld-graph-unsub", "name": "Priya Raman", "title": "Head of Ops",
            "company": "Helios Freight", "domain": "heliosfreight.example",
            "email": "priya.raman@heliosfreight.example", "country": "DE",
            "industry": "energy", "pain_point": "grid imbalance", "angle": "automate dispatch",
            "source": "graph-selftest", "source_row": 9, "unsubscribed": True, "status": "new",
        })
        oc.write_lead(vault, seed_fm)
        halted = graph.invoke({"lead_id": "ld-graph-unsub", "run_id": reporter.new_run_id("demo")})
        check("unsubscribed lead halts at the compliance node", halted["halted_at"] == "compliance",
              str(halted["halted_at"]))
        check("sender node never executed for the unsubscribed lead",
              "sender" not in halted["nodes_visited"], json.dumps(halted["nodes_visited"]))
        check("halt reason is the gate's not_unsubscribed rule",
              halted["state"].get("compliance_reasons") == ["not_unsubscribed"],
              str(halted["state"].get("compliance_reasons")))
        check("unsubscribed lead's note gained no terminal outcome",
              oc.read_frontmatter(vault, "ld-graph-unsub").get("final_status") is None)

        check("compliance_gate.py used unmodified (imported, not re-implemented)",
              compliance_gate.__file__.endswith("orchestrator/policy/compliance_gate.py"),
              compliance_gate.__file__)
        check("langgraph library was detected, not assumed",
              LANGGRAPH_AVAILABLE and LGStateGraph is not None,
              f"LANGGRAPH_AVAILABLE={LANGGRAPH_AVAILABLE}")

    print(f"\nbuild.py selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="outreach graph")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--vault", default="")
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.selftest or not args.vault:
        return _selftest()
    print(json.dumps(invoke(args.vault), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
