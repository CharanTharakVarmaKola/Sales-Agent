"""tests/test_campaign.py — Batch B5 integration tests (pytest, dry-run only).

Full dry-run path, double-invocation dedupe, guard+chain, 4-seam matrix,
A2 triple-layer byte identity, D5 campaign-scale intent. No network,
no credentials, no vendor imports.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.campaign import assemble_envelope, run_lead_campaign  # noqa: E402
from orchestrator.graph import TransitionGraph  # noqa: E402
from orchestrator.inference.dry_run import DryRunInferenceClient  # noqa: E402
from orchestrator.inference.routing import RoutingInferenceClient  # noqa: E402
from orchestrator.nodes import (  # noqa: E402
    draft_node, handoff_node, on_handoff, on_paused, run_and_persist)
from orchestrator.store import Store  # noqa: E402
from orchestrator.timeutil import utcnow_iso  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "b5.db"))
    s.init_schema()
    s.set_stop(stopped=False, reason="test release", actor="test")
    return s


def seed_lead(store, tag="b5"):
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


def paused_campaign_graph():
    g = TransitionGraph()
    g.add_node("draft", fn=draft_node).add_node("paused_term").add_node("done")
    g.add_edge("draft", "paused_term", condition=on_paused())
    g.add_edge("draft", "done")
    g.set_entry("draft").set_terminal(["paused_term", "done"])
    return g


def router():
    return RoutingInferenceClient(DryRunInferenceClient(), DryRunInferenceClient())


def counts(store):
    return (len(store.fetchall("SELECT * FROM audit")),
            len(store.fetchall("SELECT * FROM pending_export")))


def test_full_dry_run_path(store):
    lid = seed_lead(store, "full")
    env = run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "full"},
                            graph=paused_campaign_graph(), inference=router(),
                            run_id="full1", actor="t")
    assert env.exit_reason == "terminal_reached"
    assert env.path == ["draft", "paused_term"]
    assert env.paused is True and "dry_run" in (env.pause_reason or "")
    assert env.last_route["paused"] is True
    assert env.quarantined is False and env.chain_ok is True
    assert env.audit_id > 0 and env.outbox_id > 0
    row = store.fetchone("SELECT payload_json FROM audit WHERE id=?", (env.audit_id,))
    assert json.loads(row["payload_json"])["run_id"] == "full1"


def test_double_invocation_dedupes_actions_not_attempts(store):
    lid = seed_lead(store, "dbl")
    g = TransitionGraph(max_node_visits=1)
    g.add_node("a").add_node("b")
    g.add_edge("a", "b").add_edge("b", "a")
    g.set_entry("a")
    run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "dbl"},
                      graph=g, inference=router(), run_id="dbl1", actor="t")
    run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "dbl"},
                      graph=g, inference=router(), run_id="dbl1", actor="t")
    quar = store.fetchall("SELECT * FROM actions WHERE action_type='quarantine_flag'")
    assert len(quar) == 1  # headline: no duplicate action row
    audits = [json.loads(r["payload_json"]) for r in store.fetchall(
        "SELECT payload_json FROM audit WHERE event='traversal.loop_guard'")]
    assert sum(1 for p in audits if p.get("run_id") == "dbl1") == 2  # two attempts
    ok, msg = store.verify_chain()
    assert ok, msg


def test_guard_mid_campaign_chain_verifies(store):
    lid = seed_lead(store, "grd")
    g = TransitionGraph(max_transitions=2)
    g.add_node("a").add_node("b").add_node("c")
    g.add_edge("a", "b").add_edge("b", "c").add_edge("c", "b")
    g.set_entry("a")
    env = run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "grd"},
                            graph=g, inference=router(), run_id="grd1", actor="t")
    assert env.exit_reason == "max_transitions" and env.quarantined is True
    assert env.chain_ok is True


def test_seam_matrix(store):
    lid = seed_lead(store, "sz")

    def boom(s, c):
        raise RuntimeError("node-entry")

    g1 = TransitionGraph()
    g1.add_node("x", fn=boom).add_node("t")
    g1.add_edge("x", "t")
    g1.set_entry("x").set_terminal(["t"])
    a0, o0 = counts(store)
    with pytest.raises(RuntimeError):
        run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "s1"},
                          graph=g1, inference=router(), run_id="s1", actor="t")
    assert counts(store) == (a0, o0)  # seam 1 node-entry: zero rows

    from orchestrator.inference.base import InferenceClient

    class PostBlowClient(InferenceClient):
        name = "postblow"

        @property
        def paused(self):
            return (False, "")

        def draft(self, lead, evidence):
            return {"subject": "s", "body": "b", "claims": []}

    def after(s, c):
        raise RuntimeError("post-inference")

    g2 = TransitionGraph()
    g2.add_node("d", fn=draft_node).add_node("after", fn=after).add_node("t")
    g2.add_edge("d", "after").add_edge("after", "t")
    g2.set_entry("d").set_terminal(["t"])
    with pytest.raises(RuntimeError):
        run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "s2"},
                          graph=g2, inference=PostBlowClient(),
                          run_id="s2", actor="t")
    assert counts(store) == (a0, o0)  # seam 2 post-inference: zero rows


def test_seam_postpersist_and_prereturn(store):
    lid = seed_lead(store, "sp")
    real = Store.transact

    def dying(self, **kw):
        real(self, **kw)  # COMMIT happens inside, then the seam fails
        raise RuntimeError("post-persist")

    Store.transact = dying
    try:
        with pytest.raises(RuntimeError):
            run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "s3"},
                              graph=paused_campaign_graph(), inference=router(),
                              run_id="s3", actor="t")
    finally:
        Store.transact = real
    rows = store.fetchall("SELECT payload_json FROM audit")
    assert any(json.loads(r["payload_json"]).get("run_id") == "s3" for r in rows)
    assert len(store.fetchall("SELECT * FROM pending_export")) >= 1  # seam 3: committed

    env_first = run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "s4"},
                                  graph=paused_campaign_graph(), inference=router(),
                                  run_id="s4", actor="t")
    a1, o1 = counts(store)
    again = assemble_envelope(store, run_id="s4",
                              result=type("R", (), {"exit_reason": env_first.exit_reason,
                                                    "path": env_first.path,
                                                    "terminated_at_node":
                                                    env_first.terminated_at_node})(),
                              outbox_after_id=env_first.outbox_id - 1,
                              lead_row_id=lid)
    assert asdict(again) == asdict(env_first)  # seam 4 pre-return: re-assembly equal
    assert counts(store) == (a1, o1)  # reads mutate nothing


def test_a2_byte_identity_three_layers(store):
    seed_lead(store, "a2")
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
    env = run_lead_campaign(store, lead_row_id=None, lead={"lead_id": "a2"},
                            graph=g, inference=router(), run_id="a2x", actor="t")
    row = store.fetchone("SELECT payload_json FROM audit WHERE id=?", (env.audit_id,))
    in_audit = json.loads(row["payload_json"])["stall_events"]
    assert env.stall_events == in_audit and len(env.stall_events) == 2
    assert [e["count"] for e in env.stall_events] == [1, 2]


def test_d5_campaign_intent_end_to_end(store):
    lid = seed_lead(store, "d5")
    g = TransitionGraph()
    g.add_node("draft", fn=draft_node).add_node("route", fn=handoff_node)
    g.add_node("vip").add_node("std")
    g.add_edge("draft", "route")
    g.add_edge("route", "vip", condition=on_handoff("vip"))
    g.add_edge("route", "std")
    g.set_entry("draft").set_terminal(["vip", "std"])
    state = {"handoff": {"target": "vip", "reason": "high-score"}}
    env = run_lead_campaign(store, lead_row_id=lid, lead={"lead_id": "d5"},
                            graph=g, inference=router(), run_id="d5a",
                            actor="t", initial_state=state)
    assert env.path == ["draft", "route", "vip"]
    row = store.fetchone("SELECT payload_json FROM audit WHERE id=?", (env.audit_id,))
    trail = json.loads(row["payload_json"])["trail"]
    assert {"target": "vip", "reason": "high-score"} == {
        "target": trail[-1]["target"], "reason": trail[-1]["reason"]}


def test_no_transfer_plumbing_in_product():
    import pathlib
    root = pathlib.Path(__file__).parent.parent / "orchestrator"
    hits = []
    for p in root.rglob("*.py"):
        low = p.read_text(encoding="utf-8").lower()
        for token in ("as_tool", "subtask", "passport", "delegate"):
            if token in low:
                hits.append(f"{p.name}:{token}")
    assert hits == [], f"transfer plumbing present: {hits}"


def test_static_scans_new_code():
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "orchestrator" / "campaign.py"
           ).read_text(encoding="utf-8")
    low = src.lower()
    for token in ("http", "socket", "requests", ":20128", ":3001",
                  "v1/chat/completions", "api_key", "bearer", "secret",
                  "password", "sqlite3", "import datetime"):
        assert token not in low, f"forbidden token in campaign.py: {token}"
