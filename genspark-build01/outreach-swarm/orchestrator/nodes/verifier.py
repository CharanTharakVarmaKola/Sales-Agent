#!/usr/bin/env python3
"""orchestrator/nodes/verifier.py — EXISTING scaffold node.

Status: pre-existing, self-tested, and treated as frozen from here on.
The copywriter node (Task 2) must produce output that satisfies the contract
below; this file is not modified by any later task.

Input contract (DRAFT_CONTRACT) — a mapping with:
    lead_id        : str  non-empty
    subject        : str  1..118 chars, single line, no unresolved placeholders
    body           : str  >= 80 chars, addressed greeting, contains a question
    claims         : list[{claim: str, evidence_ref: str}]  >= 1 entry
    evidence_ids   : list[str]  evidence-graph node ids consulted

Output contract:
    {
      "verdict": "accept" | "reject",
      "checks": [{"check": str, "passed": bool, "detail": str}, ...],
      "reasons": [str, ...],           # failed checks (blocking)
      "warnings": [str, ...],          # non-blocking
      "draft_id": str,
      "lead_id": str,
      "contract_version": str,
      "live": bool,                    # always False here: pure offline validation
    }

Halt semantics: a "reject" verdict is a terminal state for that lead in the
graph — the compliance gate and sender are never reached.

Self-test:  python3 orchestrator/nodes/verifier.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CONTRACT_VERSION = "draft-contract/1"
REQUIRED_KEYS = ("lead_id", "subject", "body", "claims", "evidence_ids")
SUBJECT_MAX = 118
BODY_MIN = 80

PLACEHOLDER_RE = re.compile(r"\{\{|\}\}|\bTODO\b|lorem ipsum|\[insert", re.IGNORECASE)
GREETING_RE = re.compile(r"^\s*(hi|hello|hey|dear)\b", re.IGNORECASE | re.MULTILINE)
QUESTION_RE = re.compile(r"\?")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")
STOP = {
    "the", "and", "for", "with", "that", "this", "your", "you", "our", "are", "was", "were",
    "have", "has", "had", "from", "into", "about", "there", "their", "them", "they", "will",
    "would", "could", "should", "team", "teams", "just", "like", "more", "than", "then",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(text or "") if t.lower() not in STOP}


def _sentence_split(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def verify_draft(draft: Mapping[str, Any], evidence_graph: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Validate one draft. Pure function: no I/O, no network, no clock."""
    graph = evidence_graph or {}
    nodes: Mapping[str, Any] = graph.get("nodes") or {}
    graph_ids = set(nodes) | set(graph.get("ids") or [])
    evidence_corpus = " ".join(
        json.dumps(v, default=str) if not isinstance(v, str) else v for v in nodes.values()
    ) if isinstance(nodes, Mapping) else ""
    if not evidence_corpus:
        evidence_corpus = " ".join(str(i) for i in graph_ids)

    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []

    def check(name: str, passed: bool, detail: str, *, blocking: bool = True) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail, "blocking": blocking})
        if not passed and not blocking:
            warnings.append(f"{name}: {detail}")

    d = dict(draft or {})
    draft_id = hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]

    missing = [k for k in REQUIRED_KEYS if k not in d]
    check("draft_keys_complete", not missing, f"missing={missing}" if missing else "all required keys present")

    lead_id = str(d.get("lead_id") or "")
    check("lead_id_present", bool(lead_id), f"lead_id={lead_id!r}")

    subject = str(d.get("subject") or "")
    check("subject_present_and_bounded",
          0 < len(subject) <= SUBJECT_MAX and "\n" not in subject,
          f"len={len(subject)} max={SUBJECT_MAX} single_line={chr(10) not in subject}")

    body = str(d.get("body") or "")
    check("body_present_and_substantive", len(body) >= BODY_MIN, f"len={len(body)} min={BODY_MIN}")
    check("body_is_greeted", bool(GREETING_RE.search(body)), "greeting line found" if GREETING_RE.search(body) else "no greeting")
    check("body_has_a_question", bool(QUESTION_RE.search(body)), "call-to-action question present" if QUESTION_RE.search(body) else "no question")
    check("no_unresolved_placeholders", not PLACEHOLDER_RE.search(f"{subject}\n{body}"), "no template markers")

    claims = d.get("claims") or []
    check("claims_present", isinstance(claims, list) and len(claims) >= 1, f"claims={len(claims) if isinstance(claims, list) else 'not-a-list'}")

    bad_refs: List[str] = []
    empty_claims: List[int] = []
    for i, c in enumerate(claims if isinstance(claims, list) else []):
        if not isinstance(c, Mapping):
            bad_refs.append(f"#{i}: not a mapping")
            continue
        ref = str(c.get("evidence_ref") or "")
        text = str(c.get("claim") or "").strip()
        if not text:
            empty_claims.append(i)
        if not ref or ref not in graph_ids:
            bad_refs.append(f"#{i}: evidence_ref={ref!r} not in evidence graph ({len(graph_ids)} ids)")
    check("claims_are_non_empty", not empty_claims, f"empty claim indexes={empty_claims}")
    check("claims_have_evidence_refs", not bad_refs, "; ".join(bad_refs) if bad_refs else "every claim resolves to an evidence node")

    listed = [str(x) for x in (d.get("evidence_ids") or [])]
    dangling = [x for x in listed if x not in graph_ids]
    check("evidence_ids_consistent", not dangling, f"dangling={dangling}" if dangling else f"{len(listed)} ids all resolve")

    # numeric assertions in the body must be attested somewhere in the evidence
    unbacked: List[str] = []
    for sentence in _sentence_split(body):
        if not re.search(r"\d", sentence):
            continue
        toks = _tokens(sentence)
        overlap = len(toks & _tokens(evidence_corpus)) / max(1, len(toks))
        if overlap < 0.5:
            unbacked.append(sentence[:90])
    check("no_unbacked_numeric_assertions", not unbacked,
          "; ".join(unbacked) if unbacked else "every numeric sentence is attested in evidence")

    # every claim should be traceable into the body (warn, don't block)
    body_tokens = _tokens(body)
    for i, c in enumerate(claims if isinstance(claims, list) else []):
        if isinstance(c, Mapping):
            ct = _tokens(str(c.get("claim") or ""))
            if ct and len(ct & body_tokens) / len(ct) < 0.34:
                warnings.append(f"claim #{i} is not clearly traceable in the body text")

    reasons = [c["check"] for c in checks if not c["passed"] and c.get("blocking", True)]
    return {
        "verdict": "accept" if not reasons else "reject",
        "checks": checks,
        "reasons": reasons,
        "warnings": warnings,
        "draft_id": draft_id,
        "lead_id": lead_id,
        "contract_version": CONTRACT_VERSION,
        "live": False,
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
GOOD_GRAPH = {
    "ids": ["ev-industry-1", "ev-pain-1"],
    "nodes": {
        "ev-industry-1": {"kind": "industry", "label": "industrial automation", "text": "industrial automation sector"},
        "ev-pain-1": {"kind": "pain_point", "text": "manual QA cycles slow releases", "label": "manual QA cycles"},
    },
}
GOOD_DRAFT = {
    "lead_id": "ld-verifier-good",
    "subject": "Northwind: a short look at manual QA cycles",
    "body": (
        "Hi Marcus,\n\nI noticed Northwind Robotics operates in industrial automation. "
        "Teams there often describe manual QA cycles slowing every release. "
        "A concrete angle we could test is automating the repeatable part of that cycle.\n\n"
        "Would a short call next week be useful?\n\nBest,\nLocal-first Outreach Swarm\n"
    ),
    "claims": [
        {"claim": "Northwind Robotics operates in industrial automation", "evidence_ref": "ev-industry-1"},
        {"claim": "Manual QA cycles slow releases", "evidence_ref": "ev-pain-1"},
    ],
    "evidence_ids": ["ev-industry-1", "ev-pain-1"],
}
BAD_DRAFT = dict(GOOD_DRAFT)
BAD_DRAFT = {**GOOD_DRAFT, "claims": GOOD_DRAFT["claims"] + [
    {"claim": "Your rival lost 40% of revenue last quarter", "evidence_ref": "ev-does-not-exist-001"}]}


def _selftest() -> int:
    passed = failed = 0

    def report(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    good = verify_draft(GOOD_DRAFT, GOOD_GRAPH)
    report("grounded draft is accepted", good["verdict"] == "accept", f"reasons={good['reasons']}")
    report("accepted draft has no blocking reasons", good["reasons"] == [], str(good["reasons"]))
    report("verifier never claims to be live", good["live"] is False)

    bad = verify_draft(BAD_DRAFT, GOOD_GRAPH)
    report("hallucinated draft is rejected", bad["verdict"] == "reject", f"reasons={bad['reasons']}")
    report("rejection names claims_have_evidence_refs", "claims_have_evidence_refs" in bad["reasons"], str(bad["reasons"]))

    missing_key = {k: v for k, v in GOOD_DRAFT.items() if k != "body"}
    mk = verify_draft(missing_key, GOOD_GRAPH)
    report("draft missing 'body' is rejected", mk["verdict"] == "reject" and "draft_keys_complete" in mk["reasons"], str(mk["reasons"]))

    placeholder = {**GOOD_DRAFT, "subject": "Quick question for {{name}}"}
    ph = verify_draft(placeholder, GOOD_GRAPH)
    report("unresolved placeholder is rejected", "no_unresolved_placeholders" in ph["reasons"], str(ph["reasons"]))

    numeric = {**GOOD_DRAFT, "body": GOOD_DRAFT["body"] + "\nWe saved Zebra Logistics 91% of cycle time.\n"}
    num = verify_draft(numeric, GOOD_GRAPH)
    report("unbacked numeric assertion is rejected", "no_unbacked_numeric_assertions" in num["reasons"], str(num["reasons"]))

    report("contract version is stamped", good["contract_version"] == CONTRACT_VERSION, good["contract_version"])
    print(f"\nverifier.py selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="draft verifier (frozen scaffold node)")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return _selftest()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
