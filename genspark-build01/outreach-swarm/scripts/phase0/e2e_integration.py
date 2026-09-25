#!/usr/bin/env python3
"""scripts/phase0/e2e_integration.py — BUILD Task 5.

One fabricated lead runs the entire sequence
    ingest -> copywriter -> verifier -> compliance gate -> sender (dry-run) -> reporter
using only mocks and a local temp folder as the vault, and the script prints
EVERY intermediate output at EVERY node (full trace), not just the final result.

A second fabricated lead is pre-marked ``unsubscribed: true`` and must halt at
the compliance gate with zero calls reaching the sender.

Run:  python3 scripts/phase0/e2e_integration.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.graph import build as graph_build  # noqa: E402
from orchestrator.policy import reporter  # noqa: E402

CLEAN_CSV = """Full Name,ORG,E-Mail Address,Website,Country Code,Sector,Pain,Hook,Opt Out
Marcus Vale,Northwind Robotics,marcus.vale@northwindrobotics.example,northwindrobotics.example,GB,industrial automation,manual QA cycles,cut QA time,no
"""

UNSUB_CSV = """Full Name,ORG,E-Mail Address,Website,Country Code,Sector,Pain,Hook,Opt Out
Priya Raman,Helios Freight,priya.raman@heliosfreight.example,heliosfreight.example,DE,logistics,dispatch delays,automate dispatch,yes
"""

EVIDENCE = {
    "ev-industry-automation.md": {"id": "ev-ind-automation", "kind": "industry", "label": "industrial automation",
                                  "text": "industrial automation buyers cite release-cycle pressure",
                                  "match": "industrial automation", "related": ["ev-pain-manual-qa"]},
    "ev-pain-manual-qa.md": {"id": "ev-pain-manual-qa", "kind": "pain_point", "label": "manual QA cycles",
                             "text": "manual QA cycles slow every release", "related": ["ev-angle-cut-qa"]},
    "ev-angle-cut-qa.md": {"id": "ev-angle-cut-qa", "kind": "angle", "label": "automate the repeatable part",
                           "text": "automating the repeatable part of the QA cycle", "related": ["ev-prior-outcome-northwind"]},
    "ev-prior-outcome-northwind.md": {"id": "ev-prior-outcome-northwind", "kind": "prior_outcome",
                                      "label": "prior outcome",
                                      "text": "a comparable buyer cut release-gate time after automating intake",
                                      "related": []},
    "ev-industry-logistics.md": {"id": "ev-ind-logistics", "kind": "industry", "label": "logistics",
                                 "text": "logistics operators run on manual dispatch", "match": "logistics",
                                 "related": ["ev-pain-dispatch"]},
    "ev-pain-dispatch.md": {"id": "ev-pain-dispatch", "kind": "pain_point", "label": "dispatch delays",
                            "text": "manual dispatch delays every route change", "related": ["ev-angle-dispatch"]},
    "ev-angle-dispatch.md": {"id": "ev-angle-dispatch", "kind": "angle", "label": "automate dispatch",
                             "text": "automating dispatch removes manual re-keying", "related": []},
}


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def show(label: str, payload: object) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(payload, indent=2, default=str))


def main() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="e2e-") as tmp:
        tmpd = Path(tmp)
        vault = tmpd / "vault"
        outbox = tmpd / "outbox"
        csv_path = tmpd / "clean_leads.csv"
        csv_path.write_text(CLEAN_CSV, encoding="utf-8")
        unsub_csv = tmpd / "unsub_leads.csv"
        unsub_csv.write_text(UNSUB_CSV, encoding="utf-8")

        oc.ensure_vault(vault)
        for fname, fm in EVIDENCE.items():
            oc.write_note(vault, vault / "Evidence" / fname, fm, f"\n{fm['text']}\n")

        # ================= LEAD 1: clean, full chain =================
        banner("LEAD 1 — clean lead: ingest -> copywriter -> verifier -> compliance -> sender(dry-run) -> reporter")
        run_id = reporter.new_run_id("e2e")
        g = graph_build.build_graph(vault, outbox=outbox)
        print("\nvault (temp):", vault)
        print("outbox (temp):", outbox)
        print("outbound provider:", "none — sender dry-run only")
        print("run_id:", run_id)

        result = g.invoke({"csv_path": str(csv_path), "run_id": run_id})

        for entry in result["trace"]:
            node = entry["node"]
            out = entry["output"]
            banner(f"NODE OUTPUT: {node}")
            if node == "ingest":
                show("ingest result", out)
            elif node == "copywriter":
                show("copywriter draft", out.get("draft"))
                show("evidence graph walk", (out.get("copywriter") or {}).get("evidence_graph"))
            elif node == "verifier":
                show("verifier verdict", out)
            elif node == "compliance":
                show("compliance decision", out)
            elif node == "sender":
                show("sender result", out)
            elif node == "reporter":
                show("reporter run-log entry", out)

        show("final graph state (terminal outcome)", {
            "nodes_visited": result["nodes_visited"],
            "completed": result["completed"],
            "halted_at": result["halted_at"],
        })

        lead_id = result["state"]["lead_id"]
        note_text = oc.read_note(vault, lead_id)
        banner(f"VAULT STATE AFTER LEAD 1 — {lead_id}")
        print(note_text)
        fm = oc.read_frontmatter(vault, lead_id)

        check("lead 1 visited every node in order",
              result["nodes_visited"] == ["ingest", "copywriter", "verifier", "compliance", "sender", "reporter"],
              json.dumps(result["nodes_visited"]))
        check("lead 1 draft was accepted by the verifier",
              (result["state"].get("verifier") or {}).get("verdict") == "accept",
              str(result["state"].get("verdict")))
        check("lead 1 passed the compliance gate", result["state"].get("compliance_decision") == "allow")
        check("lead 1 sender ran in dry-run mode",
              (result["state"].get("sender") or {}).get("status") == "dry_run")
        check("lead 1 made ZERO external HTTP calls",
              (result["state"].get("sender") or {}).get("external_http_calls") == [],
              str((result["state"].get("sender") or {}).get("external_http_calls")))
        check("lead 1 outbox artifact written (dry run, not delivered)",
              bool((result["state"].get("sender") or {}).get("outbox_path")))
        check("report run-log entry written to the note", "stage=reporter" in note_text)
        check("terminal frontmatter written by the reporter",
              fm.get("final_status") == "dry_run_complete" and fm.get("compliance_verdict") == "allow"
              and fm.get("run_id") == run_id and fm.get("delivery_status") == "dry_run",
              json.dumps({k: fm.get(k) for k in sorted(oc.TERMINAL_KEYS)}))
        check("dry run did not increment send_count", int(fm.get("send_count") or 0) == 0, str(fm.get("send_count")))

        # ================= LEAD 2: unsubscribed, must halt =================
        banner("LEAD 2 — pre-marked unsubscribed: must halt at the compliance gate, zero sender calls")
        run_id2 = reporter.new_run_id("e2e")
        g2 = graph_build.build_graph(vault, outbox=outbox)
        result2 = g2.invoke({"csv_path": str(unsub_csv), "run_id": run_id2})
        for entry in result2["trace"]:
            banner(f"NODE OUTPUT: {entry['node']}")
            show(f"{entry['node']} result", entry["output"])
        show("final graph state (halted)", {
            "nodes_visited": result2["nodes_visited"],
            "completed": result2["completed"],
            "halted_at": result2["halted_at"],
            "halt_reason": result2["halt_reason"],
        })

        lead2 = result2["state"]["lead_id"]
        fm2 = oc.read_frontmatter(vault, lead2)
        check("lead 2 halted at the compliance node", result2["halted_at"] == "compliance",
              str(result2["halted_at"]))
        check("lead 2 never reached the sender node", "sender" not in result2["nodes_visited"],
              json.dumps(result2["nodes_visited"]))
        check("lead 2 block reason is not_unsubscribed",
              (result2["state"].get("compliance_reasons") or [None]) == ["not_unsubscribed"],
              str(result2["state"].get("compliance_reasons")))
        check("lead 2 produced no message id and no outbox file",
              not list(Path(outbox).glob(f"{lead2}*")) if Path(outbox).exists() else True)
        check("lead 2 note has no terminal outcome written", fm2.get("final_status") is None,
              json.dumps({k: fm2.get(k) for k in sorted(oc.TERMINAL_KEYS)}))

        banner(f"VAULT STATE AFTER LEAD 2 — {lead2}")
        print(oc.read_note(vault, lead2))

    print(f"\ne2e_integration: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
