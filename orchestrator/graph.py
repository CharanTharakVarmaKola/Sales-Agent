"""orchestrator/graph.py — TransitionGraph mirror (Phase B, Batch B3).

Implements docs/parity-B3.md as amended (A1/A2/A3) and divergences D1-D5;
where the B3 batch order and the parity spec differ, the parity spec won
(no conflicts found — this note records the check). ARCHITECTURE.md B3
contract applies.

- Pure Python, stdlib only. No network/LLM calls in this module. Nodes are
  injected callables (B4 wires real ones); the graph owns structure and
  traversal only.
- Edge evaluation mirrors AG2 _select: registration order, first match
  wins; unconditional edge always matches and ends evaluation; no match is
  the default-terminate fall-through (condition_met).
- Terminals are finish-nodes (A1/D5): fn executes, outgoing edges are
  skipped unevaluated, exit is terminal_reached. Handoff transfer plumbing
  is out of scope until B4.
- Guards never raise: max_transitions (exact-N, default 100) and the
  per-node revisit cap (default 3, D1) return structured results. Stall
  watchers (A2) record non-traversal events without consuming budget.
- Node-fn AND condition-fn exceptions propagate unchanged (A3); per-run
  state is method-local, so the instance stays clean and reusable.
- Determinism: same graph + same fns + same (state, ctx) -> same path.
  No wall-clock reads; time arrives via ctx["now"] only.
- Convention (B1 MINOR 1): no I/O inside traversal, conditions, or guards.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EXIT_REASONS = ("terminal_reached", "condition_met",
                "loop_guard", "max_transitions")


@dataclass
class TerminationResult:
    exit_reason: str
    path: list[str]
    visits: dict[str, int]
    transitions_taken: int
    terminated_at_node: str
    stall_events: list[dict] = field(default_factory=list)
    last_route: dict | None = None  # B4 fills from just-observed outcomes only


class GraphError(ValueError):
    """Build-time or run-start validation failure. Never raised mid-run."""


class TransitionGraph:
    """Registration-ordered conditional graph with non-raising guards."""

    def __init__(self, max_transitions: int = 100, max_node_visits: int = 3):
        self._nodes: dict[str, object | None] = {}
        self._edges: dict[str, list[tuple[str, object | None]]] = {}
        self._terminals: set[str] = set()
        self._entry: str | None = None
        self._stall_guards: dict[str, int] = {}
        self.max_transitions = max_transitions
        self.max_node_visits = max_node_visits
        # No per-run state on self — run() allocates everything locally.

    def add_node(self, key: str, fn=None) -> "TransitionGraph":
        self._nodes[key] = fn
        return self

    def add_edge(self, src: str, dst: str, condition=None) -> "TransitionGraph":
        if src not in self._nodes:
            raise GraphError(f"add_edge: unknown src {src!r}")
        if dst not in self._nodes:
            raise GraphError(f"add_edge: unknown dst {dst!r}")
        self._edges.setdefault(src, []).append((dst, condition))
        return self

    def set_entry(self, key: str) -> "TransitionGraph":
        if key not in self._nodes:
            raise GraphError(f"set_entry: unknown node {key!r}")
        self._entry = key
        return self

    def set_terminal(self, keys) -> "TransitionGraph":
        for key in keys:
            if key not in self._nodes:
                raise GraphError(f"set_terminal: unknown node {key!r}")
            self._terminals.add(key)
        return self

    def add_stall_guard(self, node: str, threshold: int = 1) -> "TransitionGraph":
        if node not in self._nodes:
            raise GraphError(f"add_stall_guard: unknown node {node!r}")
        self._stall_guards[node] = threshold
        return self

    def _reachable(self) -> set[str]:
        seen: set[str] = set()
        stack = [self._entry]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            for dst, _ in self._edges.get(node, []):
                stack.append(dst)
        return seen

    def run(self, state: dict, ctx: dict) -> TerminationResult:
        # Run-start validation (D4): fail fast before any traversal.
        if self._entry is None:
            raise GraphError("run: no entry node set")
        reachable = self._reachable()
        unreachable_terminals = [t for t in self._terminals if t not in reachable]
        if unreachable_terminals:
            raise GraphError(
                f"run: unreachable terminals {sorted(unreachable_terminals)}")

        # Per-run state is local: the instance stays reusable and clean.
        visits: dict[str, int] = {}
        path: list[str] = []
        stall_events: list[dict] = []
        transitions_taken = 0
        current = self._entry

        while True:
            visits[current] = visits.get(current, 0) + 1
            path.append(current)  # arrivals are audited even when guards trip
            if visits[current] > self.max_node_visits:
                return TerminationResult(
                    exit_reason="loop_guard", path=path, visits=dict(visits),
                    transitions_taken=transitions_taken,
                    terminated_at_node=current, stall_events=stall_events)

            threshold = self._stall_guards.get(current)
            if threshold is not None and visits[current] >= threshold:
                stall_events.append({"type": "stall_guard", "node": current,
                                     "count": visits[current],
                                     "ts_from_ctx": ctx.get("now")})

            fn = self._nodes[current]
            if fn is not None:
                state = fn(state, ctx)

            if current in self._terminals:
                # Finish short-circuit (A1): edges never evaluated.
                return TerminationResult(
                    exit_reason="terminal_reached", path=path,
                    visits=dict(visits), transitions_taken=transitions_taken,
                    terminated_at_node=current, stall_events=stall_events)

            matched = None
            for dst, condition in self._edges.get(current, []):
                if condition is None or condition(state, ctx):
                    matched = dst
                    break
            if matched is None:
                return TerminationResult(
                    exit_reason="condition_met", path=path,
                    visits=dict(visits), transitions_taken=transitions_taken,
                    terminated_at_node=current, stall_events=stall_events)
            if transitions_taken >= self.max_transitions:
                return TerminationResult(
                    exit_reason="max_transitions", path=path,
                    visits=dict(visits), transitions_taken=transitions_taken,
                    terminated_at_node=current, stall_events=stall_events)
            transitions_taken += 1
            current = matched
