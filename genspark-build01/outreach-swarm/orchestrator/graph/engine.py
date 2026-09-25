#!/usr/bin/env python3
"""A tiny deterministic graph engine with LangGraph-shaped semantics.

The swarm's topology is authored once, in ``orchestrator/graph/build.py``. When
``langgraph`` is importable the same topology is also assembled as a real
``StateGraph``; the engine here is what the tests execute, so the recorded traces
are byte-stable across sessions and never depend on a package version.

Semantics: a node maps ``state -> partial state update``; conditional edges map
the current state to the next node name; visiting ``END`` finishes the run and
freezes the trace, including the node at which a halt occurred.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

END = "__end__"


class GraphError(RuntimeError):
    pass


class StateGraph:
    def __init__(self, name: str = "graph", *, max_steps: int = 50) -> None:
        self.name = name
        self.max_steps = max_steps
        self.nodes: Dict[str, Callable[[Dict[str, Any]], Mapping[str, Any]]] = {}
        self.edges: Dict[str, str] = {}
        self.branches: Dict[str, Callable[[Dict[str, Any]], str]] = {}
        self.branch_targets: Dict[str, Dict[str, str]] = {}
        self.entry: Optional[str] = None

    # -- authoring --------------------------------------------------------
    def add_node(self, name: str, fn: Callable[[Dict[str, Any]], Mapping[str, Any]]) -> "StateGraph":
        if name == END:
            raise GraphError(f"{END} is reserved")
        self.nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self.edges[src] = dst
        return self

    def add_conditional_edges(
        self,
        src: str,
        router: Callable[[Dict[str, Any]], str],
        mapping: Optional[Mapping[str, str]] = None,
    ) -> "StateGraph":
        def routed(state: Dict[str, Any], _router=router, _mapping=dict(mapping or {})) -> str:
            key = _router(state)
            return _mapping.get(key, key)

        self.branches[src] = routed
        self.branch_targets[src] = dict(mapping or {})
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self.entry = name
        return self

    # -- introspection ----------------------------------------------------
    def topology(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "nodes": list(self.nodes),
            "edges": dict(self.edges),
            "conditional": sorted(self.branches),
            "branch_targets": {k: dict(v) for k, v in self.branch_targets.items()},
            "entry": self.entry,
        }

    # -- execution --------------------------------------------------------
    def invoke(self, state: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        if not self.entry:
            raise GraphError("no entry point set")
        current: Dict[str, Any] = dict(state or {})
        trace: List[Dict[str, Any]] = []
        halted_at: Optional[str] = None

        node = self.entry
        for _ in range(self.max_steps):
            if node == END:
                break
            fn = self.nodes.get(node)
            if fn is None:
                raise GraphError(f"unknown node {node!r}")
            out = fn(current) or {}
            update = dict(out)
            current.update(update)
            trace.append({"node": node, "update_keys": sorted(update), "output": _safe(update)})

            if node in self.branches:
                nxt = self.branches[node](current)
            else:
                nxt = self.edges.get(node, END)
            if nxt == END:
                halted_at = node
                break
            node = nxt
        else:  # pragma: no cover - guards against an accidental cycle
            raise GraphError(f"graph exceeded max_steps={self.max_steps}")

        halted = halted_at is not None and bool(current.get("halted"))
        return {
            "graph": self.name,
            "state": current,
            "trace": trace,
            "nodes_visited": [t["node"] for t in trace],
            "halted_at": halted_at if halted else None,
            "completed": halted_at is not None and not halted,
            "halt_reason": current.get("halt_reason"),
        }


def _safe(obj: Any) -> Any:
    """JSON-safe projection of a node update for the trace."""
    if isinstance(obj, Mapping):
        return {k: _safe(v) for k, v in obj.items() if k not in {"evidence_graph"}}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
