"""orchestrator/campaign.py — B5 one-entry campaign dry-run.

Implements docs/parity-B5.md §§1-3 (Hub post_envelope analog: traverse →
persist atomically → read-back envelope; reads never mutate). Exactly ONE
Store.transact per run (B4 shape; no new transaction semantics here).
D5: handoff INTENT routes at campaign scale; transfer-execution stays
scoped out (no personas execute in dry-run) — no transfer plumbing here.

Convention: no I/O in this module beyond Store reads/writes via the Store
API and run_and_persist. Time via timeutil. Dry-run posture throughout.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from orchestrator.nodes import run_and_persist
from orchestrator.store import Store
from orchestrator.timeutil import utcnow_iso


@dataclass
class CampaignEnvelope:
    run_id: str
    lead_row_id: int | None
    exit_reason: str
    path: list
    terminated_at_node: str
    quarantined: bool
    paused: bool
    pause_reason: str | None
    last_route: dict | None
    stall_events: list
    audit_id: int
    outbox_id: int
    chain_ok: bool


def _payload_for_run(store: Store, event: str, run_id: str) -> tuple[int, dict]:
    rows = store.fetchall(
        "SELECT id, payload_json FROM audit WHERE event=? ORDER BY id DESC LIMIT 10",
        (event,))
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("run_id") == run_id:
            return row["id"], payload
    raise KeyError(f"no audit payload for run {run_id}")


def assemble_envelope(store: Store, *, run_id: str, result,
                      outbox_after_id: int,
                      lead_row_id: int | None) -> CampaignEnvelope:
    """Pure-read assembly (P-B5-12): second call returns equal envelope,
    zero new rows. All derivations come from the stored audit payload."""
    audit_id, payload = _payload_for_run(
        store, f"traversal.{result.exit_reason}", run_id)
    trail = payload.get("trail", [])
    outbox_rows = store.fetchall(
        "SELECT id FROM pending_export WHERE id > ? ORDER BY id", (outbox_after_id,))
    if len(outbox_rows) != 1:
        raise AssertionError(
            f"expected exactly one outbox row for run {run_id}, "
            f"found {len(outbox_rows)}")
    paused_entries = [e for e in trail if e.get("outcome") == "paused"]
    chain_ok, _ = store.verify_chain()
    return CampaignEnvelope(
        run_id=run_id,
        lead_row_id=lead_row_id,
        exit_reason=result.exit_reason,
        path=list(result.path),
        terminated_at_node=result.terminated_at_node,
        quarantined=bool(
            result.exit_reason in ("loop_guard", "max_transitions")
            or any(e.get("outcome") == "flagged" for e in trail)),
        paused=bool(paused_entries),
        pause_reason=(paused_entries[0].get("reason") if paused_entries else None),
        last_route=payload.get("last_route"),
        stall_events=payload.get("stall_events", []),
        audit_id=audit_id,
        outbox_id=outbox_rows[0]["id"],
        chain_ok=chain_ok,
    )


def run_lead_campaign(store: Store, *, lead_row_id: int | None, lead: dict,
                      graph, inference, run_id: str, actor: str,
                      entity_type: str = "campaign",
                      initial_state: dict | None = None) -> CampaignEnvelope:
    """One orchestrated call: ctx → run_and_persist (ONE transact) →
    read-back envelope. Node raises propagate with zero rows written."""
    before = store.fetchone("SELECT COALESCE(MAX(id), 0) AS m FROM pending_export")["m"]
    ctx = {"now": utcnow_iso(), "run_id": run_id, "inference": inference}
    state = {"lead": lead}
    if initial_state:
        state.update(initial_state)
    result = run_and_persist(graph, state, ctx, store, actor=actor,
                             run_id=run_id, entity_type=entity_type,
                             lead_row_id=lead_row_id)
    return assemble_envelope(store, run_id=run_id, result=result,
                             outbox_after_id=before, lead_row_id=lead_row_id)
