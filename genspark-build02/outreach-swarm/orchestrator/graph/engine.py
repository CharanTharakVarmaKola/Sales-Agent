"""orchestrator/graph/engine.py — deterministic LangGraph-shaped engine.

The `langgraph` package is detected and its StateGraph imported when present,
but recorded traces use this local engine so results are stable across
package versions.
"""
from __future__ import annotations

try:
    from langgraph.graph import StateGraph  # type: ignore
    LANGGRAPH_AVAILABLE = True
except Exception:
    StateGraph = None
    LANGGRAPH_AVAILABLE = False


class LocalStateGraph:
    """Minimal deterministic StateGraph: add_node / add_edge / conditional edges."""

    def __init__(self):
        self.nodes: dict[str, callable] = {}
        self.edges: dict[str, str] = {}
        self.conditional: dict[str, callable] = {}
        self.entry: str | None = None

    def add_node(self, name: str, fn: callable):
        self.nodes[name] = fn
        return self

    def set_entry_point(self, name: str):
        self.entry = name
        return self

    def add_edge(self, a: str, b: str):
        self.edges[a] = b
        return self

    def add_conditional_edges(self, a: str, route: callable):
        self.conditional[a] = route
        return self

    def compile(self):
        graph = self

        class Runnable:
            def invoke(self, state: dict) -> dict:
                state = dict(state)
                state.setdefault("_visited", [])
                current = graph.entry
                guard = 0
                while current and current != "__end__":
                    guard += 1
                    if guard > 100:
                        raise RuntimeError("graph did not terminate")
                    state["_visited"].append(current)
                    state = graph.nodes[current](state) or state
                    if current in graph.conditional:
                        current = graph.conditional[current](state)
                    else:
                        current = graph.edges.get(current, "__end__")
                return state

        return Runnable()


def build_graph():
    return LocalStateGraph()
