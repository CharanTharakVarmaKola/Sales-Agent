"""orchestrator/nodes.py — B4 graph-injected nodes + persist runner.

Implements docs/parity-B4.md §§2-5 (AG2 ask-round/fold mapping, D5 intent,
R-LASTROUTE-1/2, boundary rules). ARCHITECTURE.md Gate 3 + B4 carries apply.

Purity model: node fns perform NO I/O and read time only from ctx["now"].
Traversal state flows through returned mappings (edge conditions read
state). Audit-relevant outcomes ALSO go to ctx["trail"] — a runner-owned,
per-run, write-only channel the runner creates fresh on every call
(nodes never read it, conditions never touch it; Q may grep-assert this).
The runner persists trail + TerminationResult in ONE Store.transact.

Inference errors: InferencePaused is caught BY NAME first at the draft
boundary and routed to the paused path with a same-frame fresh last_route
(R-LASTROUTE-1 b/c). Any other exception propagates (runner persists
nothing — B1 crash rigor at the new seam). Dry-run posture: with the B2
dry_run client every draft raises; no creds, no network, no live paths.
"""
from __future__ import annotations

import hashlib
import json

from orchestrator.graph import TerminationResult, TransitionGraph
from orchestrator.inference.base import InferenceClient, InferencePaused
from orchestrator.redact import redact_for_audit
from orchestrator.store import Store

PAUSED_STAGE = "paused"


class SystemStopped(Exception):
    """Raised when the stop flag is set. Dispatch and sends refuse."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _key_of(client: object) -> str:
    return getattr(client, "name", type(client).__name__)


def idempotency_key(lead_id: str, draft_hash: str, channel: str = "email") -> str:
    """Rule 7 key. Pure (hashlib only). Format pinned by test."""
    return hashlib.sha1(
        f"{lead_id}|{draft_hash}|{channel}".encode("utf-8")).hexdigest()


def _trail(ctx: dict) -> list | None:
    trail = ctx.get("trail")
    return trail if isinstance(trail, list) else None


def draft_node(state: dict, ctx: dict) -> dict:
    """One inference call per visit. Quarantine checked BEFORE any call."""
    if state.get("quarantined") or state.get("quarantine_reason"):
        new = {**state, "draft_skipped": "quarantined", "paused": False}
        trail = _trail(ctx)
        if trail is not None:
            trail.append({"node": "draft", "outcome": "skipped-quarantined"})
        return new
    client: InferenceClient = ctx["inference"]
    lead = state["lead"]
    evidence = state.get("evidence", [])
    try:
        out = client.draft(lead, evidence)
    except InferencePaused as exc:
        raw = getattr(client, "last_route", None)
        if isinstance(raw, dict):
            route = dict(raw)  # R-1(b): set by this client's raising call
        else:
            route = {"paused": True, "reason": exc.reason,  # R-1(c)
                     "primary": _key_of(client), "fallback": None}
        new = {**state, "paused": True, "pause_reason": exc.reason,
               "last_route": route}
        trail = _trail(ctx)
        if trail is not None:
            trail.append({"node": "draft", "outcome": "paused",
                          "reason": exc.reason, "last_route": dict(route)})
        return new
    raw = getattr(client, "last_route", None)
    if isinstance(raw, dict):
        route = dict(raw)  # R-1(b): set by this client's returning call
    else:
        route = {"paused": False, "reason": "",  # plain client: observed here
                 "primary": _key_of(client), "fallback": None}
    new = {**state, "draft": out, "paused": False, "last_route": route}
    trail = _trail(ctx)
    if trail is not None:
        trail.append({"node": "draft", "outcome": "drafted",
                      "last_route": dict(route)})
        # B6-LIVE triple-gate addition: metering side-channel. If the client
        # recorded usage for THIS call, attach it (model/tokens/cost-basis
        # only — never credentials). Necessity: per-call spend ledger;
        # B4 pins re-run green; recorded in CHANGE-NOTES.
        usage = getattr(client, "last_usage", None)
        if isinstance(usage, dict):
            trail.append({"node": "draft", "outcome": "metered",
                          "usage": dict(usage)})
    return new


def quarantine_check(state: dict, ctx: dict) -> dict:
    """Pure flagging. Never sends, never calls inference."""
    flagged = bool(state.get("quarantine_reason")
                   or state.get("lead", {}).get("stage") == "quarantined")
    new = {**state, "quarantined": flagged}
    trail = _trail(ctx)
    if trail is not None:
        trail.append({"node": "quarantine_check", "outcome": "flagged"
                      if flagged else "clear"})
    return new


def handoff_node(state: dict, ctx: dict) -> dict:
    """D5 intent observation. Transfer across agents stays out of scope
    (re-carried to B5); routing happens through on_handoff edges."""
    intent = state.get("handoff")
    new = {**state, "handoff_observed": intent is not None}
    trail = _trail(ctx)
    if trail is not None and intent is not None:
        trail.append({"node": "handoff", "outcome": "intent",
                      "target": intent.get("target"),
                      "reason": intent.get("reason", "")})
    return new


def on_handoff(target: str):
    """Edge condition factory: matches a D5 routing intent."""
    return lambda s, c: (s.get("handoff") or {}).get("target") == target


def on_paused():
    """Edge condition factory: routes to the paused-terminal path."""
    return lambda s, c: bool(s.get("paused"))


def on_quarantined():
    """Edge condition factory: routes to the quarantine path."""
    return lambda s, c: bool(s.get("quarantined"))


def _draft_hash(draft: dict) -> str:
    return hashlib.sha256(
        json.dumps(draft, sort_keys=True).encode("utf-8")).hexdigest()


def run_and_persist(graph: TransitionGraph, state: dict, ctx: dict,
                    store: Store, *, actor: str, run_id: str,
                    entity_type: str = "traversal",
                    lead_row_id: int | None = None) -> TerminationResult:
    """Run traversal, then persist EVERYTHING in one B1-shape transact.

    Node-fn raises propagate with zero rows written. Guard outcomes
    (loop_guard/max_transitions) become quarantine_flag action rows —
    never silent drops. Stall events ride the audit payload. last_route is
    copied verbatim from the trail (R-LASTROUTE-2); absent → null.
    Action rows need a FK-valid lead: pass lead_row_id (tests seed one);
    when None, quarantine/guard outcomes are audit-only and the payload
    records "unaffiliated": True (no invented customer semantics).
    """
    ctx = {**ctx, "trail": []}  # fresh per run: no cross-run leakage
    stop = store.get_stop()
    if stop.get("stopped"):
        raise SystemStopped(stop.get("reason") or "system stopped")
    result = graph.run(state, ctx)
    trail = ctx["trail"]

    last_route = None
    for entry in trail:
        if "last_route" in entry:
            last_route = entry["last_route"]  # latest observation wins

    payload = {
        "run_id": run_id,
        "exit_reason": result.exit_reason,
        "path": result.path,
        "visits": result.visits,
        "transitions_taken": result.transitions_taken,
        "terminated_at_node": result.terminated_at_node,
        "stall_events": result.stall_events,
        "last_route": last_route,  # R-2: verbatim copy or null
        "trail": trail,
    }
    needs_quarantine_row = result.exit_reason in ("loop_guard", "max_transitions") \
        or any(e.get("outcome") == "flagged" for e in trail)
    if needs_quarantine_row and lead_row_id is None:
        payload["unaffiliated"] = True

    def fn(conn):
        # NOTE: send_draft rows are NOT auto-created here; B5 dispatch owns
        # them via persist_draft_action (Rule 7). This txn carries audit +
        # outbox (auto) plus quarantine/guard action rows only.
        if needs_quarantine_row and lead_row_id is not None:
            key = (f"guard-{run_id}-{result.exit_reason}" if result.exit_reason
                   in ("loop_guard", "max_transitions") else f"quar-{run_id}")
            conn.execute(
                "INSERT OR IGNORE INTO actions (lead_id, action_type, status,"  # B5 §2: retries dedupe
                " idempotency_key, batch_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (lead_row_id, "quarantine_flag", "queued", key, run_id,
                 ctx["now"], ctx["now"]))
        return payload

    store.transact(actor=actor, event=f"traversal.{result.exit_reason}",
                   entity_type=entity_type,
                   entity_id=lead_row_id if lead_row_id is not None else 0,
                   payload=redact_for_audit(payload), fn=fn)
    return result


def persist_draft_action(store: Store, *, lead_row_id: int, draft: dict,
                         run_id: str, now: str, actor: str) -> str:
    """Rule 7 send_draft row with INSERT OR IGNORE. Returns the key."""
    lead_id = str(draft.get("lead_id", lead_row_id))
    key = idempotency_key(lead_id, _draft_hash(draft))

    def fn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO actions (lead_id, action_type, status,"
            " idempotency_key, draft_hash, batch_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (lead_row_id, "send_draft", "queued", key, _draft_hash(draft),
             run_id, now, now))
        return key

    store.transact(actor=actor, event="action.send_draft_queued",
                   entity_type="actions", entity_id=lead_row_id,
                   payload={"idempotency_key": key}, fn=fn)
    return key
