"""orchestrator/inbound.py — hostile-surface reply loop (dry, fake-verified).

Implements parity-B6 §11 (R5). Poll → dedupe (distinct keyspace) → exact
linkage → quarantine-by-default → audit → terminal pre-filter. READS
actions, NEVER dispatches: no send-side reference may appear in this
module (Q grep-pins). Fetcher failures propagate with zero rows written
(fail closed). LLM classification stays paused; terminal signals are
deterministic regex GATES (Rule 3), never deciders. Convention: no I/O
beyond the Store API; time via ctx now-string.
"""
from __future__ import annotations

import re

UNSUB_RE = re.compile(
    r"\b(unsubscribe|remove me|take me off|opt.?out|stop emailing|"
    r"do not email|don.?t contact me)\b", re.I)
BOUNCE_RE = re.compile(
    r"(undeliverable|delivery (status|failure)|mail delivery failed|"
    r"address not found|mailbox (full|unavailable)|user unknown|"
    r"returned mail|no longer (at|with) this (address|company))", re.I)


INBOUND_BATCH_MAX = 100


def poll(fetcher, limit: int = 25) -> list[dict]:
    """Call the injected fetcher. Raises propagate (fail closed upstream)."""
    envelopes = fetcher(limit)
    if not isinstance(envelopes, list):
        raise ValueError("fetcher broke the envelope contract")
    return envelopes[:limit]


def _terminal_of(body: str) -> str | None:
    if UNSUB_RE.search(body or ""):
        return "unsubscribe"
    if BOUNCE_RE.search(body or ""):
        return "bounce"
    return None


def reconcile(store, envelopes: list[dict], *, actor: str, run_id: str,
              now: str, lead_row_id: int | None = None) -> dict:
    """One transact for the batch: dedupe → link → quarantine → audit.

    Returns {fetched, inserted, duplicates, rejected, quarantined,
    terminals}. Unknown references open NEW threads (never fuzzy-attached);
    id-less envelopes are audit-rejected loudly; terminal hits move the lead
    stage (closed/paused) through the deterministic gate only. Batches
    larger than INBOUND_BATCH_MAX are rejected before any write so one
    misbehaving fetcher cannot hold a giant transaction.
    """
    if len(envelopes) > INBOUND_BATCH_MAX:
        raise ValueError(
            f"batch {len(envelopes)} exceeds limit {INBOUND_BATCH_MAX}")
    outcomes: list[dict] = []

    def fn(conn):
        for env in envelopes:
            if not isinstance(env, dict):
                outcomes.append({"message_id": None, "outcome": "rejected",
                                 "reason": "malformed-envelope"})
                continue
            mid = env.get("message_id")
            if not mid:
                outcomes.append({"message_id": None, "outcome": "rejected",
                                 "reason": "missing-id"})
                continue
            thread_id = None
            ref = env.get("in_reply_to")
            if ref:
                hit = conn.execute(
                    "SELECT thread_id FROM messages WHERE provider_message_id=?",
                    (ref,)).fetchone()
                if hit:
                    thread_id = hit["thread_id"]
            unlinked = False
            if thread_id is None:
                if lead_row_id is None:
                    outcomes.append({"message_id": mid, "outcome": "rejected",
                                     "reason": "no-lead-anchor"})
                    continue
                thread_id = conn.execute(
                    "INSERT INTO threads (lead_id, channel, status, subject,"
                    " run_id, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (lead_row_id, env.get("channel", "email"), "active",
                     env.get("subject"), run_id, now, now)).lastrowid
                unlinked = True
            body = env.get("body", "")
            terminal = _terminal_of(body)
            cur = conn.execute(
                "INSERT OR IGNORE INTO messages (thread_id, direction, body_raw,"
                " quarantine_reason, provider_message_id, received_at,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (thread_id, "inbound", body, "untrusted-inbound", mid, now,
                 now, now))
            if cur.rowcount == 0:
                outcomes.append({"message_id": mid, "outcome": "duplicate"})
                continue
            if terminal == "unsubscribe" and lead_row_id is not None:
                conn.execute("UPDATE leads SET stage='closed', updated_at=? "
                             "WHERE id=?", (now, lead_row_id))
            elif terminal == "bounce" and lead_row_id is not None:
                conn.execute("UPDATE leads SET stage='paused', updated_at=? "
                             "WHERE id=?", (now, lead_row_id))
            outcomes.append({"message_id": mid, "outcome": "inserted",
                             "thread_id": thread_id, "unlinked": unlinked,
                             "terminal": terminal})
        return outcomes

    # NOTE: payload references the live `outcomes` list populated by fn()
    # below; Store.transact redacts centrally AFTER fn runs, so do NOT
    # pre-redact here (a copy would freeze the still-empty list).
    store.transact(actor=actor, event="inbound.reconciled",
                   entity_type="leads",
                   entity_id=lead_row_id if lead_row_id is not None else 0,
                   payload={"run_id": run_id, "outcomes": outcomes}, fn=fn)
    summary = {"fetched": len(envelopes), "inserted": 0, "duplicates": 0,
               "rejected": 0, "quarantined": 0, "terminals": []}
    for o in outcomes:
        if o["outcome"] == "inserted":
            summary["inserted"] += 1
            summary["quarantined"] += 1
            if o["terminal"]:
                summary["terminals"].append(o["message_id"])
        elif o["outcome"] == "duplicate":
            summary["duplicates"] += 1
        else:
            summary["rejected"] += 1
    return summary
