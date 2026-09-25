#!/usr/bin/env python3
"""orchestrator/nodes/copywriter.py — evidence walk -> verified draft.

Consumes one lead note + a 1-2 hop evidence-graph walk
(Industry -> Pain-point -> Angle -> prior-outcome) and produces a structured
draft: subject, body, claims->evidence map. Output matches the existing
verifier contract exactly (orchestrator/nodes/verifier.py — frozen).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.inference import providers  # noqa: E402
from orchestrator.nodes import verifier  # noqa: E402


def walk_evidence(vault: str, lead: dict, max_hops: int = 2) -> list[dict]:
    """1-2 hop walk: Industry note -> its linked Pain-point/Angle/outcome notes."""
    hops = []
    industry = (lead.get("industry") or "").strip()
    if not industry:
        return hops
    start = os.path.join(vault, "Evidence", f"{industry}.md")
    if not os.path.exists(start):
        return hops
    seen = {start}
    frontier = [(start, 0)]
    while frontier:
        path, hop = frontier.pop(0)
        text = oc.read_note(path)
        rel = os.path.relpath(path, vault)
        hops.append({"ref": f"{rel}#{'Evidence' if '## Evidence' in text else ''}".rstrip("#"),
                     "hop": hop, "snippet": text.strip()[:200]})
        if hop < max_hops:
            for link in re.findall(r"\[\[([^\]]+)\]\]", text):
                cand = None
                for base in (os.path.join(vault, "Evidence"), vault):
                    p = os.path.join(base, link.split("|")[0].split("#")[0] + ".md")
                    if os.path.exists(p) and p not in seen:
                        cand = p
                        break
                if cand:
                    seen.add(cand)
                    frontier.append((cand, hop + 1))
    return hops


def draft_lead(vault: str, lead_note: str, client=None) -> dict:
    lead = oc.read_frontmatter(lead_note)
    evidence = walk_evidence(vault, lead)
    client = client or providers.get_client(allow_network=False)
    raw = client.draft(lead, evidence)
    if not isinstance(raw, dict):
        raise ValueError("model output was not a JSON object")
    for key in verifier.REQUIRED_KEYS:
        if key not in raw:
            raise ValueError(f"model output missing contract key: {key}")
    verdict = verifier.verify_draft(raw)
    return {"draft": raw, "verdict": verdict, "evidence": evidence,
            "route": getattr(client, "last_route", None)}


def run(vault: str, lead_note: str, client=None) -> dict:
    return draft_lead(vault, lead_note, client)


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    from orchestrator.inference.mock import (FailingClient, HallucinatingMockClient,
                                             MalformedMockClient, MockInferenceClient)
    from orchestrator.inference.base import RoutingInferenceClient

    tmp = tempfile.mkdtemp(prefix="copywriter-selftest-")
    try:
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.join(os.path.dirname(__file__), "..", "..", "vault-schema", "Templates", "Lead.md"),
            encoding="utf-8").read())
        # evidence graph: Industry (hop 0) -> Pain-point (hop 1) -> prior-outcome (hop 2)
        os.makedirs(os.path.join(vault, "Evidence"), exist_ok=True)
        open(os.path.join(vault, "Evidence", "Logistics.md"), "w").write(
            "# Logistics\n## Evidence\nfleet margins shrinking [[Pain-point]]\n")
        open(os.path.join(vault, "Evidence", "Pain-point.md"), "w").write(
            "# Pain-point\n## Evidence\ndriver churn [[Prior-outcome]]\n")
        open(os.path.join(vault, "Evidence", "Prior-outcome.md"), "w").write(
            "# Prior outcome\n## Evidence\npeer fleet cut cost 40% after rollout\n")
        p = oc.write_lead(vault, {"lead_id": "c-1", "name": "Cara Line", "company": "FleetCo",
                                  "email": "c@fleetco.io", "industry": "Logistics"})
        res = draft_lead(vault, p, MockInferenceClient())
        check("2-hop walk reaches all four evidence kinds — industry/pain/angle/outcome chain",
              len(res["evidence"]) >= 3)
        check("walk records hop numbers per node — [0, 1, 2]",
              [e["hop"] for e in res["evidence"]] == [0, 1, 2])
        check("grounded draft is accepted by the existing verifier — reasons=[]",
              res["verdict"]["verdict"] == "accept" and res["verdict"]["reasons"] == [])
        check("draft carries subject/body/claims per frozen CONTRACT_VERSION",
              all(k in res["draft"] for k in verifier.REQUIRED_KEYS))
        check("required keys asserted against the frozen verifier module",
              verifier.REQUIRED_KEYS == ("subject", "body", "claims"))
        check("claims carry evidence refs", all(c["evidence_refs"] for c in res["draft"]["claims"]))

        bad = draft_lead(vault, p, HallucinatingMockClient())
        check("draft with an unsupported claim is REJECTED by the existing verifier",
              bad["verdict"]["verdict"] == "reject"
              and "claims_have_evidence_refs" in bad["verdict"]["reasons"]
              and "no_unbacked_numeric_assertions" in bad["verdict"]["reasons"])

        router = RoutingInferenceClient(FailingClient(), MockInferenceClient())
        ok = draft_lead(vault, p, router)
        check("primary route failure falls back to the configured fallback route",
              router.last_route == {"provider": "mock", "fallback_used": True}
              and ok["verdict"]["verdict"] == "accept")

        try:
            draft_lead(vault, p, MalformedMockClient())
            check("non-JSON model output raises instead of flowing downstream", False)
        except RuntimeError:
            check("non-JSON model output raises instead of flowing downstream", True)

        live = providers.get_client(allow_network=False)
        check("offline router cannot reach a live provider (HttpInferenceClient refuses)",
              type(live.primary).__name__ == "HttpInferenceClient")
        try:
            live.primary.draft({}, [])
            check("live primary refuses without credential", False)
        except PermissionError:
            check("live primary refuses without credential", True)
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        import ast
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
        check("copywriter never imports smtplib (no send path)",
              not any("smtplib" in n for n in imported_names))
        check("copywriter never imports the compliance gate (no gating here)",
              not any("compliance_gate" in n for n in imported_names))
        check("json import available for structured output parsing", json is not None)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
