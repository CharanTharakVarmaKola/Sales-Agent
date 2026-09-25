#!/usr/bin/env python3
"""scripts/phase0/validate_verification_loop.py

Phase-0 validator for the copywriter -> verifier loop: a grounded draft must be
accepted, an ungrounded one rejected, and the verifier must be the same frozen
module the graph calls (no second, silently-diverging copy of the rules).

Run:  python3 scripts/phase0/validate_verification_loop.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.inference import MockInferenceClient, build_inference_client  # noqa: E402
from orchestrator.nodes import copywriter, verifier  # noqa: E402

LEAD = {
    "lead_id": "ld-loop-01", "name": "Ana Duarte", "title": "Head of Ops",
    "company": "Riverbend Legal", "domain": "riverbendlegal.example",
    "email": "ana.duarte@riverbendlegal.example", "country": "PT",
    "industry": "legal services", "pain_point": "manual intake", "angle": "automate intake",
    "source": "loop-validator", "source_row": 1, "unsubscribed": False, "status": "new",
}
EVIDENCE = {
    "ev-industry-legal.md": {"id": "ev-ind-legal", "kind": "industry", "label": "legal services",
                             "text": "legal services firms run on manual intake", "related": ["ev-pain-intake"]},
    "ev-pain-intake.md": {"id": "ev-pain-intake", "kind": "pain_point", "label": "manual intake",
                          "text": "manual intake delays matter onboarding", "related": ["ev-angle-intake"]},
    "ev-angle-intake.md": {"id": "ev-angle-intake", "kind": "angle", "label": "automate intake",
                           "text": "automating intake removes duplicate entry", "related": ["ev-prior-legal"]},
    "ev-prior-legal.md": {"id": "ev-prior-legal", "kind": "prior_outcome", "label": "prior outcome",
                          "text": "a comparable firm shortened intake turnaround", "related": []},
}


def main() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="verify-loop-") as tmp:
        vault = Path(tmp) / "vault"
        oc.ensure_vault(vault)
        oc.write_lead(vault, oc.order_like_schema(LEAD))
        for fname, fm in EVIDENCE.items():
            oc.write_note(vault, vault / "Evidence" / fname, fm, f"\n{fm['text']}\n")

        good = copywriter.run(vault, "ld-loop-01", client=MockInferenceClient("grounded"))
        check("grounded draft accepted", good["verification"]["verdict"] == "accept",
              str(good["verification"]["reasons"]))

        bad = copywriter.run(vault, "ld-loop-01", client=MockInferenceClient("hallucinating"))
        check("ungrounded draft rejected", bad["verification"]["verdict"] == "reject",
              str(bad["verification"]["reasons"]))
        check("rejection cites the evidence-reference rule",
              "claims_have_evidence_refs" in bad["verification"]["reasons"],
              str(bad["verification"]["reasons"]))

        # the verifier is data-driven: rebuilding it from the contract accepts the same draft
        check("verifier exposes a versioned contract", verifier.CONTRACT_VERSION == "draft-contract/1",
              verifier.CONTRACT_VERSION)
        keys = set(verifier.REQUIRED_KEYS)
        check("copywriter emits exactly the verifier's required keys",
              keys.issubset(set(good["draft"])), str(sorted(keys - set(good["draft"]))))

        router = build_inference_client("mock", fail_primary=True)
        fb = copywriter.run(vault, "ld-loop-01", client=router)
        check("primary-route failure falls back and still verifies",
              fb["draft"]["inference"]["fallback_used"] and fb["verification"]["verdict"] == "accept",
              str(fb["draft"]["inference"]))

    print(f"\nvalidate_verification_loop: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
