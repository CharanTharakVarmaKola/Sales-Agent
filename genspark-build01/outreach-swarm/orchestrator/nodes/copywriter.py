#!/usr/bin/env python3
"""orchestrator/nodes/copywriter.py — BUILD Task 2.

Consumes one lead note plus a one-to-two-hop evidence graph walk
(Industry -> Pain-point -> Angle -> prior-outcome, per the compatibility
report's memory section) and produces the structured draft the *existing*
verifier node expects.

The verifier is never modified: the draft this node emits carries exactly
``orchestrator/nodes/verifier.py::REQUIRED_KEYS`` and the claims/evidence-ref
shape that ``verify_draft`` validates.

Inference goes through the ``InferenceClient`` interface in
``orchestrator/inference/`` where OmniRoute is the primary route and FreeLLMAPI
the fallback — both as configuration only; tests use the mock client.

Self-test:  python3 orchestrator/nodes/copywriter.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.inference import (  # noqa: E402
    InferenceClient,
    InferenceRequest,
    MockInferenceClient,
    build_inference_client,
)
from orchestrator.nodes import verifier  # noqa: E402

#: Memory-walk depth design from the compatibility report: 1 hop = industry ->
#: pain point, 2 hops = -> angle -> prior outcome. Never deeper than 2.
KIND_ORDER = ("industry", "pain_point", "angle", "prior_outcome")


# ---------------------------------------------------------------------------
# Evidence graph (read-only walk; no LLM involved)
# ---------------------------------------------------------------------------
def _frontmatter_notes(vault: str | Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    root = Path(vault) / "Evidence"
    if not root.exists():
        return out
    for path in sorted(root.rglob("*.md")):
        try:
            fm = oc.read_frontmatter(vault, path)
        except Exception:
            continue
        fm["_path"] = str(path)
        out.append(fm)
    return out


def _related_ids(fm: Mapping[str, Any]) -> List[str]:
    rel = fm.get("related")
    if isinstance(rel, str):
        return [r.strip() for r in rel.strip("[]").split(",") if r.strip()]
    if isinstance(rel, (list, tuple)):
        return [str(r) for r in rel]
    return []


def build_evidence_graph(vault: str | Path, lead: Mapping[str, Any], *, max_hops: int = 2) -> Dict[str, Any]:
    """Walk Industry -> Pain-point -> Angle -> prior-outcome for one lead."""
    notes = _frontmatter_notes(vault)
    by_id = {str(n.get("id")): n for n in notes if n.get("id")}
    by_kind: Dict[str, List[Dict[str, Any]]] = {k: [] for k in KIND_ORDER}
    for n in notes:
        k = str(n.get("kind") or "")
        if k in by_kind:
            by_kind[k].append(n)

    def matches(note: Mapping[str, Any], needle: str) -> bool:
        if not needle:
            return False
        hay = " ".join(str(note.get(x, "")) for x in ("label", "text", "title", "match", "tags", "industry"))
        return needle.lower() in hay.lower()

    walk: List[Dict[str, Any]] = []
    picked: Dict[str, Dict[str, Any]] = {}

    def seed(kind: str) -> Optional[Dict[str, Any]]:
        for term in (lead.get("industry"), lead.get("company"), lead.get("pain_point")):
            for n in by_kind.get(kind, []):
                if matches(n, str(term or "")):
                    return n
        return None

    root = seed("industry") or (by_kind["industry"][0] if by_kind["industry"] else None)
    if root:
        picked["industry"] = root
        walk.append({"hop": 0, "kind": "industry", "id": root.get("id"), "via": "industry match"})

    # hop 1 and hop 2: follow `related` links, respecting KIND_ORDER
    frontier = [root] if root else []
    for hop in (1, 2):
        if hop > max_hops or not frontier:
            break
        nxt: List[Dict[str, Any]] = []
        for node in frontier:
            for rid in _related_ids(node):
                cand = by_id.get(rid)
                if not cand:
                    continue
                kind = str(cand.get("kind") or "")
                if kind == "industry":
                    continue
                if kind not in picked:
                    picked[kind] = cand
                    walk.append({"hop": hop, "kind": kind, "id": cand.get("id"),
                                 "via": f"related:{node.get('id')}"})
                    nxt.append(cand)
        frontier = nxt

    nodes = {str(n.get("id")): {k: v for k, v in n.items() if k != "_path"} for n in picked.values() if n.get("id")}
    return {
        "lead_id": lead.get("lead_id"),
        "nodes": nodes,
        "ids": [str(n.get("id")) for n in picked.values() if n.get("id")],
        "kinds": {k: (str(v.get("id")) if v.get("id") else None) for k, v in picked.items()},
        "walk": walk,
        "max_hops": max_hops,
        "vault_evidence_notes": len(notes),
    }


# ---------------------------------------------------------------------------
# Prompt assembly (real code; the route behind it is configuration)
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = """You are drafting a single outbound email for a local-first outreach swarm.

LEAD
{lead_json}

EVIDENCE GRAPH WALK (hops={hops})
{evidence_json}

RULES
1. Every factual statement about the lead must be traceable to one evidence node id above.
2. Return JSON with keys: lead_id, subject (<=118 chars), body, claims (list of {{claim, evidence_ref}}), evidence_ids.
3. claims[].evidence_ref must be an id that appears in the evidence graph.
4. No unsupported numbers, no placeholders, one clear call-to-action question.
"""


def build_prompt(lead: Mapping[str, Any], graph: Mapping[str, Any]) -> str:
    return PROMPT_TEMPLATE.format(
        lead_json=json.dumps({k: lead.get(k) for k in
                              ("lead_id", "name", "title", "company", "domain", "industry", "country", "pain_point", "angle")},
                             indent=2),
        hops=graph.get("max_hops"),
        evidence_json=json.dumps(graph.get("nodes", {}), indent=2),
    )


def draft_from_client(
    lead: Mapping[str, Any],
    graph: Mapping[str, Any],
    client: InferenceClient,
) -> Dict[str, Any]:
    """Call the route, parse JSON, and coerce the result onto the contract."""
    request = InferenceRequest(
        prompt=build_prompt(lead, graph),
        system="You write grounded B2B outreach drafts and output JSON only.",
        purpose="copywriter.draft",
        response_format="json",
        metadata={"lead": dict(lead), "evidence_graph": graph, "evidence_ids": graph.get("ids", [])},
    )
    result = client.complete(request)
    try:
        payload = json.loads(result.text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"copywriter route {result.provider} did not return JSON: {exc}") from exc

    draft = {
        "lead_id": payload.get("lead_id") or lead.get("lead_id"),
        "subject": str(payload.get("subject") or "").strip(),
        "body": str(payload.get("body") or ""),
        "claims": [dict(c) for c in (payload.get("claims") or []) if isinstance(c, Mapping)],
        "evidence_ids": [str(x) for x in (payload.get("evidence_ids") or graph.get("ids", []))],
        "inference": {**result.to_dict(), "purpose": request.purpose},
        "evidence_walk": graph.get("walk", []),
    }
    missing = [k for k in verifier.REQUIRED_KEYS if k not in draft]
    if missing:
        raise ValueError(f"copywriter produced a draft missing {missing}")
    if not draft["subject"] or not draft["body"]:
        raise ValueError("copywriter produced an empty subject or body")
    return draft


# ---------------------------------------------------------------------------
# Node entry point
# ---------------------------------------------------------------------------
def run(
    vault: str | Path,
    lead: Any,
    *,
    client: Optional[InferenceClient] = None,
    persist: bool = True,
    verify: bool = True,
) -> Dict[str, Any]:
    """Graph node: lead note + evidence walk -> verified draft."""
    lead_id = lead.get("lead_id") if isinstance(lead, Mapping) else lead
    fm = oc.read_frontmatter(vault, lead_id)
    client = client or build_inference_client("mock")
    graph = build_evidence_graph(vault, fm)

    draft = draft_from_client(fm, graph, client)

    if persist:
        drafts_dir = Path(vault) / "_drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        (drafts_dir / f"{lead_id}.json").write_text(json.dumps(draft, indent=2), encoding="utf-8")
        oc.patch_section(
            vault, lead_id, "## Evidence",
            "**Evidence graph walk (max 2 hops)**\n\n"
            + "\n".join(f"- hop {w['hop']}: `{w['id']}` ({w['kind']}) via {w['via']}" for w in graph["walk"])
            + f"\n\n**Draft subject:** {draft['subject']}\n"
            + "\n**Claims → evidence**\n"
            + "\n".join(f"- {c.get('claim')} → `{c.get('evidence_ref')}`" for c in draft["claims"]),
        )

    verification = verifier.verify_draft(draft, graph) if verify else None
    return {
        "node": "copywriter",
        "lead_id": lead_id,
        "draft": draft,
        "verification": verification,
        "evidence_graph": {"ids": graph["ids"], "kinds": graph["kinds"],
                           "walk": graph["walk"], "nodes": graph["nodes"]},
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
EVIDENCE_NOTES = {
    "ev-industry-automation.md": {
        "id": "ev-industry-automation", "kind": "industry", "label": "industrial automation",
        "text": "industrial automation buyers cite release-cycle pressure",
        "match": "industrial automation", "related": ["ev-pain-manual-qa", "ev-angle-cut-qa"],
    },
    "ev-pain-manual-qa.md": {
        "id": "ev-pain-manual-qa", "kind": "pain_point", "label": "manual QA cycles",
        "text": "manual QA cycles slow every release", "related": ["ev-angle-cut-qa", "ev-prior-outcome-northwind"],
    },
    "ev-angle-cut-qa.md": {
        "id": "ev-angle-cut-qa", "kind": "angle", "label": "automate the repeatable part",
        "text": "automating the repeatable part of the QA cycle", "related": ["ev-prior-outcome-northwind"],
    },
    "ev-prior-outcome-northwind.md": {
        "id": "ev-prior-outcome-northwind", "kind": "prior_outcome", "label": "prior outcome",
        "text": "a comparable buyer cut release-gate time after automating intake", "related": [],
    },
}
LEAD = {
    "lead_id": "ld-copywriter-01", "name": "Marcus Vale", "title": "VP Engineering",
    "company": "Northwind Robotics", "domain": "northwindrobotics.example",
    "email": "marcus.vale@northwindrobotics.example", "country": "GB",
    "industry": "industrial automation", "pain_point": "manual QA cycles", "angle": "cut QA time",
    "source": "copywriter-selftest", "source_row": 1, "unsubscribed": False, "status": "new",
}


def _seed_vault(vault: Path) -> None:
    oc.ensure_vault(vault)
    oc.write_lead(vault, oc.order_like_schema(LEAD))
    for fname, fm in EVIDENCE_NOTES.items():
        body = f"\n## Evidence note\n\n{fm['text']}\n\nRelated: {', '.join(fm['related']) or 'none'}\n"
        oc.write_note(vault, (Path(vault) / "Evidence" / fname), fm, body)


def _selftest() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="copywriter-selftest-") as tmp:
        vault = Path(tmp) / "vault"
        _seed_vault(vault)

        graph = build_evidence_graph(vault, LEAD)
        check("2-hop walk reaches all four evidence kinds",
              set(graph["kinds"]) == {"industry", "pain_point", "angle", "prior_outcome"},
              json.dumps(graph["kinds"]))
        check("walk records hop numbers per node", [w["hop"] for w in graph["walk"]] == [0, 1, 1, 2],
              str([w["hop"] for w in graph["walk"]]))

        # --- case 1: grounded draft passes the EXISTING verifier -----------
        res_good = run(vault, "ld-copywriter-01", client=MockInferenceClient("grounded"))
        v_good = res_good["verification"]
        check("grounded draft is accepted by the existing verifier", v_good["verdict"] == "accept",
              f"reasons={v_good['reasons']}")
        check("draft carries exactly the verifier's required keys",
              all(k in res_good["draft"] for k in verifier.REQUIRED_KEYS),
              str(sorted(verifier.REQUIRED_KEYS)))
        check("every claim resolves to a walked evidence node",
              all(c["evidence_ref"] in graph["ids"] for c in res_good["draft"]["claims"]),
              json.dumps([c["evidence_ref"] for c in res_good["draft"]["claims"]]))
        check("draft persisted for audit",
              (Path(vault) / "_drafts" / "ld-copywriter-01.json").exists())
        check("evidence walk written into the lead note's Evidence section",
              "hop 2" in oc.read_note(vault, "ld-copywriter-01"))

        # --- case 2: a claim with no supporting evidence is rejected --------
        res_bad = run(vault, "ld-copywriter-01", client=MockInferenceClient("hallucinating"))
        v_bad = res_bad["verification"]
        check("draft with an unsupported claim is REJECTED by the existing verifier",
              v_bad["verdict"] == "reject", f"reasons={v_bad['reasons']}")
        check("rejection is specifically about evidence references",
              "claims_have_evidence_refs" in v_bad["reasons"], str(v_bad["reasons"]))
        check("no evidence node was wrongly invented to justify the claim",
              "ev-does-not-exist-001" not in graph["ids"], str(graph["ids"]))

        # --- case 3: route fallback works (OmniRoute fails -> FreeLLMAPI) ---
        router = build_inference_client("mock", fail_primary=True)
        res_fb = run(vault, "ld-copywriter-01", client=router)
        inf = res_fb["draft"]["inference"]
        check("primary route failure falls back to the configured fallback route",
              inf["provider"] == "mock-freellmapi" and inf["fallback_used"] is True, json.dumps(inf))
        check("fallback draft still passes the verifier", res_fb["verification"]["verdict"] == "accept")

        # --- case 4: malformed model output is a hard error, not junk -------
        try:
            run(vault, "ld-copywriter-01", client=MockInferenceClient("malformed"))
            malformed_raised = False
        except ValueError:
            malformed_raised = True
        check("non-JSON route output raises instead of flowing downstream", malformed_raised)

        # --- configuration-only routes --------------------------------------
        routes = build_inference_client("live")
        from orchestrator.inference import InferenceError

        live_blocked = False
        try:
            routes.complete(InferenceRequest(prompt="x"))
        except InferenceError:
            live_blocked = True
        check("live OmniRoute/FreeLLMAPI routes refuse to call out without credentials", live_blocked)
        check("no HTTP call was made in this session",
              not getattr(routes.primary, "calls", []) or all(not c.get("ok") for c in routes.attempts),
              json.dumps(routes.attempts))

    print(f"\ncopywriter.py selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="copywriter node (evidence-walk -> verified draft)")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--vault", default="")
    p.add_argument("--lead", default="")
    p.add_argument("--mock-mode", default="grounded", choices=["grounded", "hallucinating", "malformed"])
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.selftest or not (args.vault and args.lead):
        return _selftest()
    out = run(args.vault, args.lead, client=MockInferenceClient(args.mock_mode))
    print(json.dumps({"draft": out["draft"], "verification": out["verification"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
