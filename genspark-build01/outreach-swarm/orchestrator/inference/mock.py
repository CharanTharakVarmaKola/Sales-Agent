#!/usr/bin/env python3
"""Mock inference client — deterministic, offline, no network.

Modes
-----
``grounded``       every claim it emits is backed by an evidence node that
                   really exists in the evidence graph it was handed.
``hallucinating``  the draft is otherwise well-formed but carries one claim
                   whose evidence reference does not exist in the evidence
                   graph — used to prove the verifier rejects it (BUILD Task 2).
``malformed``      returns text that is not JSON — used to prove the copywriter
                   surfaces a hard error instead of passing junk downstream.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

from .base import InferenceClient, InferenceRequest, InferenceResult


def _sentences(*parts: str) -> str:
    return " ".join(p.strip().rstrip(".") + "." for p in parts if p)


class MockInferenceClient(InferenceClient):
    def __init__(self, mode: str = "grounded", *, name: str = "mock-inference", fail: bool = False) -> None:
        if mode not in {"grounded", "hallucinating", "malformed"}:
            raise ValueError(f"unknown mock mode: {mode}")
        self.mode = mode
        self.name = name
        self.model = f"mock::{mode}"
        self.live = False
        self.fail = fail
        self.calls: List[Dict[str, Any]] = []

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _payload(request: InferenceRequest) -> Dict[str, Any]:
        ctx = request.metadata.get("context") or request.metadata or {}
        graph = ctx.get("evidence_graph") or {}
        return {
            "lead": ctx.get("lead", {}),
            "graph": graph,
            "nodes": graph.get("nodes") or {},
            "kinds": graph.get("kinds") or {},
            "evidence_ids": ctx.get("evidence_ids") or graph.get("ids") or [],
        }

    def _draft_grounded(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        lead = payload.get("lead") or {}
        nodes = payload.get("nodes") or {}
        kinds = payload.get("kinds") or {}
        ids = list(payload.get("evidence_ids") or [])

        def node_for(kind: str) -> Dict[str, Any]:
            nid = kinds.get(kind)
            if not nid:
                return {}
            note = dict(nodes.get(nid) or {})
            note.setdefault("id", nid)
            return note

        ind, pain, angle, prior = (node_for("industry"), node_for("pain_point"),
                                   node_for("angle"), node_for("prior_outcome"))

        name = lead.get("name") or "there"
        company = lead.get("company") or "your team"
        industry = ind.get("label") or lead.get("industry") or "your sector"
        pain_text = pain.get("text") or lead.get("pain_point") or "manual reporting"
        angle_text = angle.get("text") or lead.get("angle") or "automating the workflow"
        prior_text = prior.get("text") or ""

        claims = []
        if ind.get("id"):
            claims.append({"claim": f"{company} works in {industry}", "evidence_ref": ind["id"]})
        if pain.get("id"):
            claims.append({"claim": f"Teams in {industry} report {pain_text}", "evidence_ref": pain["id"]})
        if angle.get("id"):
            claims.append({"claim": f"The proposed angle is: {angle_text}", "evidence_ref": angle["id"]})
        if prior.get("id") and prior_text:
            claims.append({"claim": f"Prior outcome: {prior_text}", "evidence_ref": prior["id"]})

        subject = f"{company}: a 15-minute look at {pain_text}"[:118]
        body = (
            f"Hi {name},\n\n"
            + _sentences(
                f"I noticed {company} operates in {industry}",
                f"Teams there often tell us about {pain_text}",
                f"A concrete angle we could test together is {angle_text}",
            )
            + (f"\n\n{prior_text}\n" if prior_text else "\n")
            + "\nWould a short call next week be useful?\n\nBest,\nLocal-first Outreach Swarm (dry run)\n"
        )
        return {
            "lead_id": lead.get("lead_id"),
            "subject": subject,
            "body": body,
            "claims": claims,
            "evidence_ids": ids,
            "inference": {"mode": "mock", "grounded": True},
        }

    def _draft_hallucinating(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        draft = self._draft_grounded(payload)
        draft["claims"].append(
            {
                "claim": "Your rival Acme Corp just lost 40% of revenue last quarter",
                "evidence_ref": "ev-does-not-exist-001",
            }
        )
        draft["body"] += "\nP.S. We also saw Acme Corp lose 40% of revenue last quarter.\n"
        draft["inference"] = {"mode": "mock", "grounded": False}
        return draft

    # -- interface -------------------------------------------------------
    def complete(self, request: InferenceRequest) -> InferenceResult:
        self.calls.append({"purpose": request.purpose, "mode": self.mode})
        if self.fail:
            raise RuntimeError(f"{self.name} configured to fail (fallback test)")
        if self.mode == "malformed":
            return InferenceResult(text="not json at all", provider=self.name, model=self.model, live=False)
        payload = self._payload(request)
        draft = self._draft_grounded(payload) if self.mode == "grounded" else self._draft_hallucinating(payload)
        return InferenceResult(text=json.dumps(draft, indent=2), provider=self.name, model=self.model, live=False)
