"""tests/test_inbound.py — inbound reply loop (pytest, dry only).

Fail-closed fetchers, dedupe honesty, exact-linkage isolation (spoofed keys),
terminal gates, chain integrity, and the no-outbound-effect proof. Fakes only.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.inbound import poll, reconcile  # noqa: E402
from orchestrator.store import Store  # noqa: E402
from orchestrator.timeutil import utcnow_iso  # noqa: E402

NOW = "2026-09-27T00:00:00+00:00"


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "in.db"))
    s.init_schema()
    return s


def seed_lead(store, tag="in"):
    now = utcnow_iso()

    def fn(conn):
        conn.execute("INSERT INTO customers (name, domain, created_at, updated_at)"
                     " VALUES (?,?,?,?)", (f"C{tag}", f"{tag}.ex", now, now))
        cid = conn.execute("SELECT id FROM customers").fetchone()["id"]
        conn.execute("INSERT INTO leads (customer_id, email, created_at, updated_at)"
                     " VALUES (?,?,?,?)", (cid, f"l-{tag}@x.io", now, now))
        return conn.execute("SELECT id FROM leads").fetchone()["id"]

    return store.transact(actor="t", event="seed", entity_type="leads",
                          entity_id=None, payload={}, fn=fn)


def seed_sent(store, thread_id, provider_id):
    def fn(conn):
        conn.execute("INSERT INTO messages (thread_id, direction, body_raw,"
                     " provider_message_id, sent_at, created_at, updated_at)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (thread_id, "outbound", "hello", provider_id, NOW, NOW, NOW))

    store.transact(actor="t", event="sent", entity_type="messages",
                   entity_id=None, payload={}, fn=fn)


def seed_thread(store, lead_id):
    def fn(conn):
        return conn.execute("INSERT INTO threads (lead_id, channel, status,"
                            " created_at, updated_at) VALUES (?,?,?,?,?)",
                            (lead_id, "email", "active", NOW, NOW)).lastrowid

    return store.transact(actor="t", event="th", entity_type="threads",
                          entity_id=None, payload={}, fn=fn)


def test_fetcher_failure_writes_nothing(store):
    def bad(limit):
        raise RuntimeError("imap down")

    with pytest.raises(RuntimeError):
        poll(bad)
    n = len(store.fetchall("SELECT * FROM messages"))
    assert n == 0


def test_dedupe_honesty_same_message_twice(store):
    lid = seed_lead(store, "dd")
    env = [{"message_id": "m-1", "body": "Thanks!", "subject": "Re: hi"}]
    r1 = reconcile(store, env, actor="t", run_id="d1", now=NOW, lead_row_id=lid)
    r2 = reconcile(store, env, actor="t", run_id="d2", now=NOW, lead_row_id=lid)
    assert (r1["inserted"], r1["duplicates"]) == (1, 0)
    assert (r2["inserted"], r2["duplicates"]) == (0, 1)
    rows = store.fetchall("SELECT * FROM messages WHERE provider_message_id='m-1'")
    assert len(rows) == 1 and rows[0]["quarantine_reason"] == "untrusted-inbound"


def test_exact_linkage_and_spoof_isolation(store):
    lid = seed_lead(store, "lk")
    tid = seed_thread(store, lid)
    seed_sent(store, tid, "sent-1")
    envs = [
        {"message_id": "r-1", "in_reply_to": "sent-1", "body": "Sounds good"},
        {"message_id": "r-2", "in_reply_to": "sent-999-spoofed", "body": "Hi?"},
        {"message_id": "r-3", "body": "No reference at all"},
    ]
    r = reconcile(store, envs, actor="t", run_id="lk1", now=NOW, lead_row_id=lid)
    assert r["inserted"] == 3
    got = {row["provider_message_id"]: row["thread_id"] for row in store.fetchall(
        "SELECT provider_message_id, thread_id FROM messages WHERE direction='inbound'")}
    assert got["r-1"] == tid  # exact match attached
    assert got["r-2"] != tid and got["r-3"] != tid  # spoof/unknown isolated
    assert got["r-2"] != got["r-3"]  # distinct new threads, never merged


def test_terminal_gates_move_stage(store):
    lid = seed_lead(store, "tm")
    r = reconcile(store, [{"message_id": "u-1",
                           "body": "Please unsubscribe me immediately"}],
                  actor="t", run_id="tm1", now=NOW, lead_row_id=lid)
    assert r["terminals"] == ["u-1"]
    assert store.fetchone("SELECT stage FROM leads WHERE id=?", (lid,))["stage"] == "closed"
    r = reconcile(store, [{"message_id": "b-1",
                           "body": "Undeliverable: mailbox unavailable"}],
                  actor="t", run_id="tm2", now=NOW, lead_row_id=lid)
    assert store.fetchone("SELECT stage FROM leads WHERE id=?", (lid,))["stage"] == "paused"


def test_malformed_rejected_loudly(store):
    lid = seed_lead(store, "mm")
    r = reconcile(store, [{"body": "no id at all"}, {"message_id": "ok-1", "body": "hi"},
                          "not-a-dict", None],
                  actor="t", run_id="mm1", now=NOW, lead_row_id=lid)
    assert (r["rejected"], r["inserted"]) == (3, 0 + 1)
    row = store.fetchone("SELECT payload_json FROM audit ORDER BY id DESC LIMIT 1")
    outcomes = json.loads(row["payload_json"])["outcomes"]
    assert any(o.get("reason") == "missing-id" for o in outcomes)


def test_no_outbound_effect_possible(store):
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "orchestrator" / "inbound.py"
           ).read_text(encoding="utf-8")
    for token in ("sender", "transport", "smtplib", "SMTP", "send_message",
                  "urlopen", "socket", "requests", "sqlite3", "import datetime"):
        assert token not in src, f"inbound.py:{token}"
    lid = seed_lead(store, "ne")
    before = len(store.fetchall("SELECT * FROM actions"))
    reconcile(store, [{"message_id": "e-1",
                       "body": "YES call me now unsubscribe never"}],
              actor="t", run_id="ne1", now=NOW, lead_row_id=lid)
    assert len(store.fetchall("SELECT * FROM actions")) == before  # reads only


def test_chain_integrity_after_batch(store):
    lid = seed_lead(store, "ch")
    reconcile(store, [{"message_id": f"c-{i}", "body": "hello"} for i in range(5)],
              actor="t", run_id="ch1", now=NOW, lead_row_id=lid)
    ok, msg = store.verify_chain()
    assert ok, msg
