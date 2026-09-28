"""orchestrator/store.py — single SQLite Store (Phase B, Batch B1).

Implements Gate-closure spec Gate 2 (concurrency policy + resume semantics)
and Decision 8 schema (10 tables). TASK 01 §A-C: FastAPI + SQLite + Python.

Rules enforced here:
- All writes go through Store. No other module may call sqlite3.connect.
- Every connection sets PRAGMAs per-connection: WAL, NORMAL, busy_timeout,
  foreign_keys=ON. (AGENT-Q B1: verify per-connection, not per-process.)
- Mutations run BEGIN IMMEDIATE -> business SQL + audit (hash-chained) +
  pending_export (outbox) -> COMMIT, under a process-wide threading.Lock.
  Never hold a transaction across an LLM call: fn(conn) must be pure SQL.
- Crash window closed by outbox: COMMIT persists mutation + outbox row
  atomically; a later export worker drains pending_export. Killing the
  process after COMMIT loses nothing.
- Quarantine enforcement (review fix #2): fetch_for_llm() refuses any
  message with quarantine_reason set; raw quarantined text never reaches
  an LLM or sender before human approval.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import timedelta

from orchestrator.redact import redact_for_audit
from orchestrator.timeutil import utcnow, utcnow_iso

DB_PRAGMAS = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "5000"),
    ("foreign_keys", "ON"),
)

INTENTS_6WAY = (
    "interested",
    "not_interested",
    "objection",
    "auto_reply",
    "bounce",
    "unsubscribe",
)

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    domain TEXT UNIQUE,
    industry TEXT NULL,
    size_band TEXT NULL CHECK (size_band IS NULL OR size_band IN ('smb','mid','ent')),
    notes TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    email TEXT NOT NULL,
    full_name TEXT,
    stage TEXT NOT NULL DEFAULT 'new'
        CHECK (stage IN ('new','contacted','replied','quarantined','paused','closed')),
    score REAL NULL,
    source TEXT,
    next_action_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (customer_id, email)
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE RESTRICT,
    channel TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','awaiting_reply','quarantined','closed')),
    subject TEXT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound')),
    body_raw TEXT NOT NULL,
    body_sanitized TEXT NULL,
    quarantine_reason TEXT NULL,
    provider_message_id TEXT NULL,
    sent_at TEXT NULL,
    received_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    filename TEXT,
    content_hash TEXT,
    storage_path TEXT,
    trusted INTEGER NOT NULL DEFAULT 0 CHECK (trusted IN (0,1))
);
CREATE TABLE IF NOT EXISTS intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    intent TEXT NOT NULL CHECK (intent IN
        ('interested','not_interested','objection','auto_reply','bounce','unsubscribe')),
    sub_label TEXT NULL,
    confidence REAL,
    capability_ref TEXT NULL,
    model_route TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE RESTRICT,
    action_type TEXT NOT NULL
        CHECK (action_type IN ('send_draft','followup','quarantine_flag','scorecard_override')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','sent','failed','requeued','superseded')),
    idempotency_key TEXT UNIQUE NOT NULL,
    draft_hash TEXT,
    batch_id TEXT,
    worker_id TEXT NULL,
    lease_expires TEXT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    scheduled_at TEXT NULL,
    executed_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key TEXT UNIQUE NOT NULL,
    role_desc TEXT,
    persona_json TEXT,
    model_route TEXT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    payload_json TEXT,
    prev_hash TEXT,
    row_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_export (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    enqueued_ts TEXT NOT NULL,
    claimed_ts TEXT NULL,
    done_ts TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    reason TEXT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_provider_msg
    ON messages(provider_message_id) WHERE provider_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_next_action
    ON leads(next_action_at) WHERE next_action_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_actions_status_lease
    ON actions(status, lease_expires);
CREATE INDEX IF NOT EXISTS idx_threads_lead ON threads(lead_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_pending_export_done ON pending_export(done_ts);
"""


class QuarantinedContentError(PermissionError):
    """Raised when quarantined text is requested for LLM use pre-approval."""


EXPORT_LEASE_SECONDS = 600  # crashed export worker's claims become visible again


def _export_entity_id(entity_id: str | int | None) -> int:
    """Map audit entity ids to the INTEGER pending_export hint.

    None/non-numeric map to 0 (audit keeps the verbatim str id).
    """
    if entity_id is None:
        return 0
    if isinstance(entity_id, int):
        return entity_id
    try:
        return int(str(entity_id))
    except ValueError:
        return 0


def _audit_insert(conn: sqlite3.Connection, *, actor: str, event: str,
                  entity_type: str, entity_id: str | int | None,
                  payload: dict | None) -> None:
    payload_json = json.dumps(payload or {}, sort_keys=True)
    ts = utcnow_iso()
    prev_row = conn.execute(
        "SELECT row_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    prev = prev_row["row_hash"] if prev_row else "GENESIS"
    row_hash = _chain_hash(prev, ts, actor, event, entity_type,
                           None if entity_id is None else str(entity_id),
                           payload_json)
    conn.execute(
        "INSERT INTO audit (ts, actor, event, entity_type, entity_id,"
        " payload_json, prev_hash, row_hash) VALUES (?,?,?,?,?,?,?,?)",
        (ts, actor, event, entity_type,
         None if entity_id is None else str(entity_id),
         payload_json, prev, row_hash))


  # utcnow_iso is imported from orchestrator.timeutil above (carried
  # MINOR 2, B2) and re-exported here so existing callers keep working.


def _chain_hash(prev: str, ts: str, actor: str, event: str,
                entity_type: str | None, entity_id: str | None,
                payload_json: str) -> str:
    material = "|".join([prev, ts, actor, event,
                         entity_type or "", str(entity_id or ""), payload_json])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Store:
    """Single-writer SQLite store. Owns every sqlite3 connection."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # RLock: transaction bodies receive the live conn and must not call
        # back into Store helpers that re-acquire the lock; RLock keeps a
        # mistaken re-entry from deadlocking, but fn(conn) should still use
        # conn directly (see tests).
        self._lock = threading.RLock()

    # -- connections: PRAGMAs applied per-connection (not per-process) --
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        for key, value in DB_PRAGMAS:
            conn.execute(f"PRAGMA {key}={value}")
        return conn

    def probe_writable(self) -> tuple[bool, str]:
        """Public sqlite write probe under the Store lock.

        Replaces ad-hoc `store._connect()` probes in prod code; keeps the
        single-writer law (no raw sqlite3 outside this module).
        """
        with self._lock:
            conn = self._connect()
            try:
                try:
                    conn.execute("SAVEPOINT probe")
                    conn.execute("CREATE TEMP TABLE probe_write(x)")
                    conn.execute("ROLLBACK")
                    return True, "sqlite write probe ok"
                except Exception as exc:
                    return False, f"sqlite probe failed: {type(exc).__name__}"
            finally:
                conn.close()

    def init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA_DDL)
                conn.execute(
                    "INSERT OR IGNORE INTO system_state"
                    " (key, value, updated_at, updated_by, reason)"
                    " VALUES (?,?,?,?,?)",
                    ("stop", json.dumps({"stopped": True,
                                          "reason": "Default safe state. Outreach not yet enabled.",
                                          "by": "system", "at": "",
                                          "automatic": False}),
                     "", "system",
                     "Default safe state. Outreach not yet enabled."))
                conn.commit()
            finally:
                conn.close()

    def get_stop(self) -> dict:
        """Read the stop flag. Pure read; callers enforce it, never this."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM system_state WHERE key='stop'").fetchone()
            finally:
                conn.close()
        if row is None:
            return {"stopped": True, "reason": "no flag row — fail closed",
                    "by": "system", "at": "", "automatic": False}
        try:
            return json.loads(row["value"])
        except ValueError:
            return {"stopped": True, "reason": "unreadable flag — fail closed",
                    "by": "system", "at": "", "automatic": False}

    def set_stop(self, *, stopped: bool, reason: str, actor: str) -> dict:
        """Write the stop flag inside the standard audited transact."""
        now = utcnow_iso()
        record = {"stopped": bool(stopped), "reason": reason or "Operator stop",
                  "by": actor, "at": now, "automatic": False}

        def fn(conn):
            conn.execute(
                "INSERT INTO system_state (key, value, updated_at, updated_by, reason)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at, updated_by=excluded.updated_by,"
                " reason=excluded.reason",
                ("stop", json.dumps(record), now, actor, record["reason"]))

        self.transact(actor=actor,
                       event="kill.released" if not stopped else "kill.engaged",
                       entity_type="system", entity_id=None,
                       payload={"record": record}, fn=fn)
        return record

    def transact(self, *, actor: str, event: str, entity_type: str,
                 entity_id: str | int | None, payload: dict | None,
                 fn) -> object:
        """BEGIN IMMEDIATE -> fn(conn) -> audit + outbox -> COMMIT.

        fn(conn) runs business SQL only and must not do I/O or LLM calls.
        audit + pending_export inserts are inside the same transaction
        (review fix #3). Payloads are redacted centrally here. pending_export
        carries an INTEGER routing hint: numeric ids pass through,
        None/non-numeric map to 0 (audit keeps the verbatim str id).
        Returns fn's result.
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = fn(conn)
                safe_payload = redact_for_audit(payload)
                _audit_insert(conn, actor=actor, event=event,
                              entity_type=entity_type, entity_id=entity_id,
                              payload=safe_payload)
                ts = utcnow_iso()
                conn.execute(
                    "INSERT INTO pending_export (entity_type, entity_id, enqueued_ts)"
                    " VALUES (?,?,?)",
                    (entity_type,
                     _export_entity_id(entity_id), ts),
                )
                conn.execute("COMMIT")
                return result
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass  # rollback-best-effort: never mask the original error
                raise
            finally:
                conn.close()

    # -- reads (short-lived, no txn held) --
    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(sql, params).fetchone()
            finally:
                conn.close()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                return list(conn.execute(sql, params).fetchall())
            finally:
                conn.close()

    def fetch_for_llm(self, message_id: int) -> str:
        """Return LLM-safe body. Refuses quarantined rows (review fix #2)."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT body_raw, body_sanitized, quarantine_reason"
                    " FROM messages WHERE id=?", (message_id,)).fetchone()
            finally:
                conn.close()
        if row is None:
            raise KeyError(f"message {message_id} not found")
        if row["quarantine_reason"] is not None:
            raise QuarantinedContentError(
                f"message {message_id} quarantined"
                f" ({row['quarantine_reason']}): human approval required")
        return row["body_sanitized"] or row["body_raw"]

    # -- outbox worker primitives (export job = only vault writer) --
    # Worker-lease rows ARE audited (one audit row per claim/done batch) so
    # every BEGIN IMMEDIATE txn keeps the mutate + audit shape. Claims use a
    # time lease: rows claimed longer than EXPORT_LEASE_SECONDS ago with no
    # done_ts become claimable again (crash reclaim, no schema change).
    # B6-PREP triple-gate addition: optional kinds filter (default None =
    # all, preserving the B1 contract byte-for-byte). Consumers select the
    # row kinds they own so bookkeeping rows (entity pending_export) are
    # never mistaken for sendable work. Necessity: docs/parity-B6.md §2;
    # B1 pins re-run green; recorded in CHANGE-NOTES.
    def claim_exports(self, limit: int = 50,
                      kinds: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cutoff = (utcnow()
                          - timedelta(seconds=EXPORT_LEASE_SECONDS)).isoformat()
                sql = ("SELECT * FROM pending_export"
                       " WHERE done_ts IS NULL"
                       " AND (claimed_ts IS NULL OR claimed_ts <= ?)")
                params: tuple = (cutoff,)
                if kinds is not None:
                    placeholders = ",".join("?" for _ in kinds)
                    sql += f" AND entity_type IN ({placeholders})"
                    params = (cutoff, *kinds)
                rows = conn.execute(
                    sql + " ORDER BY id LIMIT ?",
                    (*params, limit)).fetchall()
                ts = utcnow_iso()
                out = [dict(r) for r in rows]
                for r in out:
                    conn.execute(
                        "UPDATE pending_export SET claimed_ts=?, attempts=attempts+1"
                        " WHERE id=?", (ts, r["id"]))
                if out:
                    _audit_insert(conn, actor="system", event="export.claimed",
                                  entity_type="pending_export", entity_id=None,
                                  payload={"ids": [r["id"] for r in out]})
                conn.execute("COMMIT")
                return out
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass  # rollback-best-effort: never mask the original error
                raise
            finally:
                conn.close()

    def mark_export_done(self, export_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE pending_export SET done_ts=? WHERE id=?",
                    (utcnow_iso(), export_id))
                _audit_insert(conn, actor="system", event="export.done",
                              entity_type="pending_export",
                              entity_id=export_id, payload={})
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass  # rollback-best-effort: never mask the original error
                raise
            finally:
                conn.close()

    def verify_chain(self) -> tuple[bool, str]:
        rows = self.fetchall(
            "SELECT ts, actor, event, entity_type, entity_id,"
            " payload_json, prev_hash, row_hash FROM audit ORDER BY id")
        prev = "GENESIS"
        for r in rows:
            expect = _chain_hash(prev, r["ts"], r["actor"], r["event"],
                                 r["entity_type"], r["entity_id"], r["payload_json"])
            if r["prev_hash"] != prev or r["row_hash"] != expect:
                return False, f"chain break at ts={r['ts']} event={r['event']}"
            prev = r["row_hash"]
        return True, f"ok ({len(rows)} rows)"
