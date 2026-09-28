"""tests/test_nodes.py — Batch B4 node tests (pytest, dry-run only).

Protocol conformance, paused-path e2e, quarantine routing, last_route
freshness (forgery), stall persistence, idempotency, D5 intent, crash and
race seams. No network, no credentials, no vendor imports.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.graph import TransitionGraph  # noqa: E402
from orchestrator.inference.base import (  # noqa: E402
    InferenceClient, InferencePaused)
from orchestrator.inference.dry_run import DryRunInferenceClient  # noqa: E402
from orchestrator.inference.routing import RoutingInferenceClient  # noqa: E402
from orchestrator.nodes import (  # noqa: E402
    draft_node, handoff_node, idempotency_key, on_handoff, on_paused,
    on_quarantined, persist_draft_action, quarantine_check, run_and_persist)
from orchestrator.store import Store  # noqa: E402
from orchestrator.timeutil import utcnow_iso  # noqa: E402

NOW = "2026-09-27T00:00:00+00:00"


class MockClient(InferenceClient):
    name = "mock-ok"

    def __init__(self):
        self.calls = 0

    @property
    def paused(self):
        return (False, "")

    def draft(self, lead, evidence):
        self.calls += 1
        return {"subject": "s", "body": "b", "claims": [],
                "lead_id": lead.get("lead_id")}


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "b4.db"))
    s.init_schema()
    s.set_stop(stopped=False, reason="test release", actor="test")
    return s


def seed_lead(store, tag="b4"):
    now = utcnow_iso()

    def fn(conn):
        conn.execute(
            "INSERT INTO customers (name, domain, created_at, updated_at)"
            " VALUES (?,?,?,?)", (f"C{tag}", f"{tag}.ex", now, now))
        cid = conn.execute("SELECT id FROM customers WHERE domain=?",
                           (f"{tag}.ex",)).fetchone()["id"]
        conn.execute(
            "INSERT INTO leads (customer_id, email, created_at, updated_at)"
            " VALUES (?,?,?,?)", (cid, f"l-{tag}@x.io", now, now))
        return conn.execute("SELECT id FROM leads").fetchone()["id"]

    return store.transact(actor="t", event="seed", entity_type="leads",
                          entity_id=None, payload={}, fn=fn)


def paused_graph():
    g = TransitionGraph()
    g.add_node("draft", fn=draft_node).add_node("paused_term").add_node("done")
    g.add_edge("draft", "paused_term", condition=on_paused())
    g.add_edge("draft", "done")
    g.set_entry("draft").set_terminal(["paused_term", "done"])
    return g


def test_protocol_purity_no_input_mutation():
    client = MockClient()
    state = {"lead": {"lead_id": "p"}, "evidence": []}
    snapshot = json.loads(json.dumps(state))
    out = draft_node(state, {"inference": client, "now": NOW})
    assert state == snapshot and out is not state
    assert out["draft"]["subject"] == "s" and client.calls == 1


def test_paused_path_end_to_end(store):
    lid = seed_lead(store, "pp")
    router = RoutingInferenceClient(DryRunInferenceClient(), DryRunInferenceClient())
    ctx = {"inference": router, "now": NOW}
    r = run_and_persist(paused_graph(), {"lead": {"lead_id": "pp"}},
                        ctx, store, actor="t", run_id="pp1", lead_row_id=lid)
    assert r.exit_reason == "terminal_reached" and r.path == ["draft", "paused_term"]
    row = store.fetchone("SELECT payload_json FROM audit ORDER BY id DESC LIMIT 1")
    payload = json.loads(row["payload_json"])
    assert payload["last_route"]["paused"] is True
    assert "dry_run" in payload["last_route"]["reason"]
    assert payload["trail"][0]["outcome"] == "paused"


def test_forgery_loses_to_fresh_record(store):
    seed_lead(store, "fg")
    forged = {"paused": False, "reason": "", "primary": "liar", "fallback": None}
    router = RoutingInferenceClient(DryRunInferenceClient())
    run_and_persist(paused_graph(), {"lead": {"lead_id": "fg"}, "last_route": forged},
                    {"inference": router, "now": NOW}, store,
                    actor="t", run_id="fg1")
    row = store.fetchone("SELECT payload_json FROM audit ORDER BY id DESC LIMIT 1")
    payload = json.loads(row["payload_json"])
    assert payload["last_route"] != forged
    assert payload["last_route"]["primary"] == "dry_run"


def test_quarantine_never_reaches_inference(store):
    lid = seed_lead(store, "qq")
    client = MockClient()
    g = TransitionGraph()
    g.add_node("qc", fn=quarantine_check).add_node("draft", fn=draft_node)
    g.add_node("qterm").add_node("done")
    g.add_edge("qc", "draft")
    g.add_edge("draft", "qterm", condition=on_quarantined())
    g.add_edge("draft", "done")
    g.set_entry("qc").set_terminal(["qterm", "done"])
    state = {"lead": {"lead_id": "qq"}, "quarantine_reason": "prompt-injection"}
    r = run_and_persist(g, state, {"inference": client, "now": NOW},
                        store, actor="t", run_id="qq1", lead_row_id=lid)
    assert client.calls == 0 and r.path == ["qc", "draft", "qterm"]
    rows = store.fetchall("SELECT * FROM actions WHERE action_type='quarantine_flag'")
    assert len(rows) == 1


def test_guard_outcome_becomes_quarantine_record(store):
    lid = seed_lead(store, "gg")
    g = TransitionGraph(max_node_visits=1)
    g.add_node("a").add_node("b")
    g.add_edge("a", "b").add_edge("b", "a")
    g.set_entry("a")
    r = run_and_persist(g, {"lead": {"lead_id": "gg"}}, {"now": NOW},
                        store, actor="t", run_id="gg1", lead_row_id=lid)
    assert r.exit_reason == "loop_guard"
    rows = store.fetchall("SELECT * FROM actions WHERE action_type='quarantine_flag'")
    assert len(rows) == 1 and "loop_guard" in rows[0]["idempotency_key"]


def test_stall_events_persist_round_trip(store):
    seed_lead(store, "st")
    g = TransitionGraph(max_transitions=50, max_node_visits=50)
    for k in ("s", "w", "t"):
        g.add_node(k)
    g.add_edge("s", "w")
    g.add_edge("w", "w", condition=lambda s, c: s.get("k", 0) < 2)
    g.add_edge("w", "t")
    g.set_entry("s").set_terminal(["t"])
    g.add_stall_guard("w", threshold=1)

    def bump(s, c):
        return {**s, "k": s.get("k", 0) + 1}

    g._nodes["w"] = bump
    r = run_and_persist(g, {"lead": {"lead_id": "st"}}, {"now": NOW},
                        store, actor="t", run_id="st1")
    assert [e["count"] for e in r.stall_events] == [1, 2]
    row = store.fetchone("SELECT payload_json FROM audit ORDER BY id DESC LIMIT 1")
    assert json.loads(row["payload_json"])["stall_events"] == r.stall_events
    ok, msg = store.verify_chain()
    assert ok, msg


def test_idempotency_key_format_and_dedupe(store):
    lid = seed_lead(store, "id")
    draft = {"lead_id": "L1", "subject": "s", "body": "b", "claims": []}
    dh = hashlib.sha256(json.dumps(draft, sort_keys=True).encode("utf-8")).hexdigest()
    key = idempotency_key("L1", dh)  # key binds lead + full draft hash + channel
    assert key == hashlib.sha1(f"L1|{dh}|email".encode("utf-8")).hexdigest()
    k1 = persist_draft_action(store, lead_row_id=lid, draft=draft,
                              run_id="r1", now=NOW, actor="t")
    k2 = persist_draft_action(store, lead_row_id=lid, draft=draft,
                              run_id="r2", now=NOW, actor="t")
    assert k1 == k2 == key
    rows = store.fetchall("SELECT * FROM actions WHERE idempotency_key=?", (key,))
    assert len(rows) == 1 and rows[0]["status"] == "queued"


def test_d5_intent_routes_and_audits_mismatch_falls_through(store):
    seed_lead(store, "h")
    for target, run, want in (("vip", "h1", ["route", "vip_lane"]),
                              ("nobody", "h2", ["route", "end"])):
        g = TransitionGraph()
        g.add_node("route", fn=handoff_node).add_node("vip_lane").add_node("end")
        g.add_edge("route", "vip_lane", condition=on_handoff("vip"))
        g.add_edge("route", "end")
        g.set_entry("route").set_terminal(["vip_lane", "end"])
        state = {"lead": {"lead_id": target},
                 "handoff": {"target": target, "reason": "score"}}
        r = run_and_persist(g, state, {"now": NOW}, store,
                            actor="t", run_id=run)
        assert r.path == want, (target, r.path)
    row = store.fetchone("SELECT payload_json FROM audit ORDER BY id DESC LIMIT 1")
    payload = json.loads(row["payload_json"])
    assert payload["trail"][0]["target"] == "nobody"


def test_node_raise_writes_nothing(store):
    seed_lead(store, "cr")

    def boom(s, c):
        raise RuntimeError("mid-pause blowup")

    g = TransitionGraph()
    g.add_node("draft", fn=boom).add_node("done")
    g.add_edge("draft", "done")
    g.set_entry("draft").set_terminal(["done"])
    n0 = len(store.fetchall("SELECT * FROM audit"))
    p0 = len(store.fetchall("SELECT * FROM pending_export"))
    with pytest.raises(RuntimeError):
        run_and_persist(g, {"lead": {"lead_id": "cr"}}, {"now": NOW},
                        store, actor="t", run_id="cr1")
    assert len(store.fetchall("SELECT * FROM audit")) == n0
    assert len(store.fetchall("SELECT * FROM pending_export")) == p0


def test_two_runs_share_store_safely(store):
    lid = seed_lead(store, "rc")
    import threading
    errors = []

    def work(tag):
        try:
            run_and_persist(paused_graph(), {"lead": {"lead_id": tag}},
                            {"inference": RoutingInferenceClient(
                                DryRunInferenceClient()), "now": NOW},
                            store, actor="t", run_id=tag, lead_row_id=lid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(f"rc{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    ok, msg = store.verify_chain()
    assert ok, msg
    assert len(store.fetchall("SELECT * FROM audit")) >= 4


def test_static_scans():
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "orchestrator" / "nodes.py"
           ).read_text(encoding="utf-8")
    low = src.lower()
    for token in ("http", "socket", "requests", ":20128", ":3001",
                  "v1/chat/completions", "api_key", "bearer", "secret",
                  "password", "refresh_token", "sqlite3", "import datetime"):
        assert token not in low, f"forbidden token in nodes.py: {token}"
