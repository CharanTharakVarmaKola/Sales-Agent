#!/usr/bin/env python3
"""e2e_integration.py — full-trace end-to-end pipeline test.

Run: ingest -> copywriter -> verifier -> compliance -> sender (dry-run) -> reporter
All mocks + a temp vault. Prints every intermediate output at every node.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.graph import build as graph_build  # noqa: E402
from orchestrator.inference.mock import MockInferenceClient  # noqa: E402

CSV_TEXT = (
    "Full Name,ORG,Work Email,Industry\n"
    "Lena Hart,Hart Analytics,l@hart.io,Analytics\n"
    "Marc Doe,PreSubbed Co,m@pre.io,Retail\n")


def main() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    tmp = tempfile.mkdtemp(prefix="e2e-")
    vault = oc.ensure_vault(os.path.join(tmp, "vault"), template=open(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                     "vault-schema", "Templates", "Lead.md")),
        encoding="utf-8").read())
    app = graph_build.build(vault, MockInferenceClient())

    # ---- Lead 1: clean CSV lead traverses all six nodes
    r1 = app.invoke({"lead": {"lead_id": "e2e-1", "name": "Lena Hart", "company": "Hart Analytics",
                              "email": "l@hart.io", "industry": "Analytics"},
                     "run_id": "run-e2e-1", "external_http_calls": []})
    print("trace lead 1:", r1["_visited"])
    check("lead 1 traversed all six nodes in order",
          r1["_visited"] == ["ingest", "copywriter", "verifier", "compliance",
                             "sender", "reporter"])
    check("lead 1 dry_run_complete", r1["send_result"]["status"] == "dry_run")
    check("lead 1 compliance_verdict=allow",
          r1["compliance_verdict"]["verdict"] == "allow")
    check("lead 1 delivery_status=dry_run", r1["send_result"]["status"] == "dry_run")
    fm1 = oc.read_frontmatter(r1["lead_note"])
    check("lead 1 run_id recorded", fm1["run_id"] == "run-e2e-1")
    check("lead 1 run-log line present",
          "[run-e2e-1]" in oc.read_note(r1["lead_note"]))
    check("lead 1 dry run did not increment send_count (0)",
          fm1["send_count"] == 0)
    check("lead 1 zero external HTTP calls", r1["external_http_calls"] == [])
    print("final note lead 1:\n", oc.read_note(r1["lead_note"]))

    # ---- Lead 2: pre-marked unsubscribed halts at compliance
    r2 = app.invoke({"lead": {"lead_id": "e2e-2", "name": "Marc Doe",
                              "company": "PreSubbed Co", "email": "m@pre.io",
                              "unsubscribed": True},
                     "run_id": "run-e2e-2", "external_http_calls": []})
    print("trace lead 2:", r2["_visited"])
    check("lead 2 halted at compliance", r2.get("halt_node") == "compliance")
    check("lead 2 sender never in visited list", "sender" not in r2["_visited"])
    check("lead 2 block reason not_unsubscribed",
          r2.get("halt_reasons") == ["not_unsubscribed"])
    check("lead 2 no message id", r2.get("send_result", {}).get("message_id") is None)
    check("lead 2 no outbox file", not r2.get("send_result", {}).get("outbox"))
    fm2 = oc.read_frontmatter(r2["lead_note"])
    check("lead 2 note has no terminal outcome written",
          fm2["final_status"] == "" and fm2["send_count"] == 0)
    check("external_http_calls empty in every run", r2["external_http_calls"] == [])

    print(f"{passed} passed, {failed} failed")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
