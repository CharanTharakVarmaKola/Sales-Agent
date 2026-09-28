"""tests/test_graph.py — Batch B3 graph tests (pytest, dry-run only).

First-class tests for the B3 order list plus AGENT-A's probe queue and the
A1/A2 amendment probes. No network, no credentials, no vendor imports.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.graph import (  # noqa: E402
    GraphError, TerminationResult, TransitionGraph)

CTX = {"now": "2026-09-27T00:00:00+00:00"}


def linear(n, **kw):
    g = TransitionGraph(**kw)
    for i in range(n + 1):
        g.add_node(f"n{i}")
    for i in range(n):
        g.add_edge(f"n{i}", f"n{i+1}")
    g.set_entry("n0").set_terminal([f"n{n}"])
    return g


def test_cycle_terminates_loop_guard_with_counters():
    g = TransitionGraph(max_node_visits=3)
    g.add_node("a").add_node("b")
    g.add_edge("a", "b").add_edge("b", "a")
    g.set_entry("a")
    r = g.run({}, CTX)
    assert r.exit_reason == "loop_guard"
    assert r.visits == {"a": 4, "b": 3}
    assert r.path == ["a", "b"] * 3 + ["a"]
    assert r.terminated_at_node == "a"


def test_max_transitions_boundary_both_directions():
    assert linear(5, max_transitions=5).run({}, CTX).exit_reason == "terminal_reached"
    r = linear(6, max_transitions=5).run({}, CTX)
    assert r.exit_reason == "max_transitions"
    assert r.transitions_taken == 5 and r.terminated_at_node == "n5"
    assert r.path == [f"n{i}" for i in range(6)]


def test_first_match_wins_ordering():
    g = TransitionGraph()
    for k in ("s", "x", "y"):
        g.add_node(k)
    g.add_edge("s", "x", condition=lambda s, c: True)
    g.add_edge("s", "y", condition=lambda s, c: True)
    g.set_entry("s").set_terminal(["x", "y"])
    assert g.run({}, CTX).path == ["s", "x"]


def test_unconditional_edge_ends_evaluation():
    seen = []

    def cond(s, c):
        seen.append(1)
        return True

    g = TransitionGraph()
    for k in ("s", "x", "y"):
        g.add_node(k)
    g.add_edge("s", "x")
    g.add_edge("s", "y", condition=cond)
    g.set_entry("s").set_terminal(["x", "y"])
    assert g.run({}, CTX).path == ["s", "x"] and seen == []


def test_no_match_terminates_condition_met():
    g = TransitionGraph()
    g.add_node("s").add_node("t")
    g.add_edge("s", "t", condition=lambda s, c: False)
    g.set_entry("s").set_terminal(["t"])
    r = g.run({}, CTX)
    assert (r.exit_reason, r.path, r.terminated_at_node) == ("condition_met", ["s"], "s")


def test_terminal_arrival_and_entry_terminal():
    g = TransitionGraph()
    g.add_node("s").add_node("t")
    g.add_edge("s", "t")
    g.set_entry("s").set_terminal(["t"])
    assert g.run({}, CTX).exit_reason == "terminal_reached"
    solo = TransitionGraph()
    solo.add_node("only")
    solo.set_entry("only").set_terminal(["only"])
    r = solo.run({}, CTX)
    assert (r.exit_reason, r.path) == ("terminal_reached", ["only"])


def test_finish_node_ignores_registered_outgoing_edge():
    calls = []

    def edge_cond(s, c):
        calls.append(1)
        return True

    ran = []

    def finish_fn(s, c):
        ran.append(1)
        return s

    g = TransitionGraph()
    g.add_node("s").add_node("fin", fn=finish_fn).add_node("escape")
    g.add_edge("s", "fin")
    g.add_edge("fin", "escape", condition=edge_cond)  # legal wiring, dead traversal
    g.set_entry("s").set_terminal(["fin", "escape"])
    r = g.run({}, CTX)
    assert r.exit_reason == "terminal_reached" and r.path == ["s", "fin"]
    assert ran == [1] and calls == []


def test_validation_constructor_vs_firstrun():
    g = TransitionGraph()
    g.add_node("a")
    with pytest.raises(GraphError):
        g.set_entry("nope")
    with pytest.raises(GraphError):
        g.add_edge("a", "nope")
    with pytest.raises(GraphError):
        g.add_edge("nope", "a")
    with pytest.raises(GraphError):
        g.set_terminal(["nope"])
    with pytest.raises(GraphError):  # missing entry: first-run
        TransitionGraph().run({}, CTX)
    unreachable = TransitionGraph()
    unreachable.add_node("s").add_node("t").add_node("lost")
    unreachable.add_edge("s", "t")
    unreachable.set_entry("s").set_terminal(["t", "lost"])
    with pytest.raises(GraphError):  # unreachable terminal: first-run
        unreachable.run({}, CTX)


def test_deterministic_replay_identical_paths():
    def fn(s, c):
        return {**s, "n": s.get("n", 0) + 1}

    g = TransitionGraph()
    g.add_node("s", fn=fn).add_node("t", fn=fn)
    g.add_edge("s", "t", condition=lambda s, c: s.get("go", False))
    g.set_entry("s").set_terminal(["t"])
    a = g.run({"go": True}, CTX)
    b = g.run({"go": True}, CTX)
    assert (a.path, b.path) == (["s", "t"], ["s", "t"])


def test_shared_state_mutation_visible_not_masked():
    calls = []

    def impure(s, c):
        calls.append(1)
        return {"parity": len(calls) % 2}

    g = TransitionGraph()
    g.add_node("s", fn=impure).add_node("t")
    g.add_edge("s", "t", condition=lambda s, c: s.get("parity") == 1)
    g.set_entry("s").set_terminal(["t"])
    first = g.run({}, CTX).path
    second = g.run({}, CTX).path
    assert first != second  # graph adds no determinism of its own
    assert len(calls) == 2  # no hidden extra invocations


def test_instance_reuse_no_residue():
    g = linear(2)
    r1 = g.run({}, CTX)
    assert g.run({}, CTX).path == r1.path
    assert vars(g).keys() <= {"_nodes", "_edges", "_terminals", "_entry",
                              "_stall_guards", "max_transitions",
                              "max_node_visits"}


def test_linear_chain_never_loop_guards():
    r = linear(10).run({}, CTX)
    assert r.exit_reason == "terminal_reached" and len(r.path) == 11


def test_stall_events_recorded_without_budget_cost():
    def bump(s, c):
        return {**s, "loop": s.get("loop", 0) + 1}

    g2 = TransitionGraph(max_transitions=100, max_node_visits=100)
    for k in ("s", "w", "t"):
        g2.add_node(k, fn=bump if k == "w" else None)
    g2.add_edge("s", "w")
    g2.add_edge("w", "w", condition=lambda s, c: s.get("loop", 0) < 3)
    g2.add_edge("w", "t")
    g2.set_entry("s").set_terminal(["t"])
    g2.add_stall_guard("w", threshold=2)
    r = g2.run({}, CTX)
    assert r.exit_reason == "terminal_reached"
    assert [e["node"] for e in r.stall_events] == ["w", "w"]
    assert [e["count"] for e in r.stall_events] == [2, 3]
    assert all(e["type"] == "stall_guard" and e["ts_from_ctx"] == CTX["now"]
               for e in r.stall_events)
    assert r.transitions_taken == 4  # s->w, w->w, w->w, w->t: events cost nothing


def test_guard_result_carries_audit_fields():
    g = TransitionGraph(max_node_visits=2)
    g.add_node("a").add_node("b")
    g.add_edge("a", "b").add_edge("b", "a")
    g.set_entry("a")
    g.add_stall_guard("a", threshold=1)
    r = g.run({}, CTX)
    assert r.exit_reason == "loop_guard"
    assert r.visits["a"] == 3 and r.stall_events[0]["count"] == 1
    assert isinstance(r.last_route, type(None))


def test_node_exception_propagates_instance_stays_clean():
    def boom(s, c):
        raise RuntimeError("node blew up")

    g = TransitionGraph()
    g.add_node("s", fn=boom).add_node("t")
    g.add_edge("s", "t")
    g.set_entry("s").set_terminal(["t"])
    with pytest.raises(RuntimeError):
        g.run({}, CTX)
    ok = TransitionGraph()
    ok.add_node("s").add_node("t")
    ok.add_edge("s", "t")
    ok.set_entry("s").set_terminal(["t"])
    assert ok.run({}, CTX).exit_reason == "terminal_reached"
    assert g.run.__self__ is g  # instance itself unharmed; reruns raise the same way
    with pytest.raises(RuntimeError):
        g.run({}, CTX)


def test_condition_exception_propagates_per_a3():
    def bad_cond(s, c):
        raise ValueError("cond blew up")

    g = TransitionGraph()
    g.add_node("s").add_node("t")
    g.add_edge("s", "t", condition=bad_cond)
    g.set_entry("s").set_terminal(["t"])
    with pytest.raises(ValueError):
        g.run({}, CTX)


def test_last_route_slot_present_but_unfilled():
    r = linear(1).run({}, CTX)
    assert r.last_route is None


def test_no_network_credential_sqlite_strings():
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "orchestrator" / "graph.py"
           ).read_text(encoding="utf-8")
    low = src.lower()
    for token in ("http", "socket", "requests", ":20128", ":3001",
                  "v1/chat/completions", "api_key", "bearer", "secret",
                  "sqlite3", "import datetime", "wall-clock".replace("-", " ")):
        assert token not in low, f"forbidden token in graph.py: {token}"
