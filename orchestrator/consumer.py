"""orchestrator/consumer.py — outbox consumer skeleton (B6-PREP).

Implements docs/parity-B6.md §§1-2,4. At-least-once with idempotent
effects: claim → attempt → done+audited; transient failure leaves the row
claimed (600 s lease reclaims it — no new Store API); attempts >=
MAX_ATTEMPTS dead-letters (done + dead_lettered audit + failed action row,
OR IGNORE). Every payload is redacted before transact (CRITICAL §4).
No network here beyond the injected sender seam (faked in tests).
"""
from __future__ import annotations

from orchestrator.live import InferencePaused
from orchestrator.redact import redact_for_audit
from orchestrator.store import Store

from orchestrator.nodes import SystemStopped

MAX_ATTEMPTS = 3


def consume_once(store: Store, *, sender, actor: str, limit: int = 25,
                 kinds: tuple[str, ...] = ("send",)) -> dict:
    """One consumer pass. Returns {claimed, sent, deferred, dead_lettered}.

    kinds selects the row kinds this consumer owns (default ("send",) —
    the B6-LIVE sendable mapping point). Bookkeeping rows (entity
    pending_export, written by this consumer's own audits) never match and
    are therefore never mistaken for sendable work.
    """
    summary = {"claimed": 0, "sent": 0, "deferred": 0, "dead_lettered": 0}
    claimed = store.claim_exports(limit=limit, kinds=kinds)
    summary["claimed"] = len(claimed)
    halted = store.get_stop()
    if halted.get("stopped"):
        raise SystemStopped(halted.get("reason") or "system stopped")
    for row in claimed:
        export_id = row["id"]
        if row["attempts"] >= MAX_ATTEMPTS:
            _dead_letter(store, export_id, actor,
                         f"attempts={row['attempts']} exhausted")
            summary["dead_lettered"] += 1
            continue
        try:
            receipt = sender.send({"export_id": export_id,
                                   "entity": row["entity_type"]},
                                  f"outbox-{export_id}")
        except InferencePaused as exc:
            # Paused sender (kill-switch/creds): hold the lease, stay silent
            # on the wire, record the hold in the summary only.
            summary["deferred"] += 1
            _audit(store, actor, "send.deferred", export_id,
                   {"reason": exc.reason})
            continue
        except Exception as exc:  # transient: lease reclaim retries it
            summary["deferred"] += 1
            _audit(store, actor, "send.transient", export_id,
                   {"error": f"{type(exc).__name__}"})
            continue
        store.mark_export_done(export_id)
        _audit(store, actor, "send.sent", export_id,
               {"receipt": receipt if isinstance(receipt, dict) else {}})
        summary["sent"] += 1
    return summary


def _audit(store: Store, actor: str, event: str, export_id: int, payload: dict) -> None:
    def fn(conn):
        return None

    store.transact(actor=actor, event=event, entity_type="pending_export",
                   entity_id=export_id,
                   payload=redact_for_audit(payload), fn=fn)


def _dead_letter(store: Store, export_id: int, actor: str, reason: str) -> None:
    # NOTE: the death is recorded in audit, not in actions — a dead row must
    # never be mistaken for a queued send (parity §2). No action row here.
    def fn(conn):
        return None

    store.transact(actor=actor, event="send.dead_lettered",
                   entity_type="pending_export", entity_id=export_id,
                   payload=redact_for_audit({"reason": reason}), fn=fn)
    store.mark_export_done(export_id)
