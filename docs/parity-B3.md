# Parity contract B3 — TransitionGraph mirror (AGENT-A, pre-build)

Source: `reference/ag2-main` @ `2f9facaf226df62f2d75a337aa240f02b58dacb7`
(read-only; verified in-session against working tree).
AGENT-C mirrors these semantics in `orchestrator/graph.py` (stdlib only).
AGENT-Q verifies against the checklist below; any divergence is a finding,
not a judgment call.

## 1. What AG2 actually does (with file:line)

**Rule shape.** A transition is one rule — `when` + `then` + `priority`
(`ag2/network/transitions.py:105-114`; docstring `:107-110`: "Lower
`priority` checks first; ties resolve in list order"). Targets and
conditions are pure: `resolve(state, envelope)` / `evaluate(state,
envelope)`, "no I/O, no awaitables" (`transitions.py:81-102`).

**Selection.** `WorkflowAdapter._select` (`ag2/network/adapters/workflow.py:470-479`):
sorted by priority, first `when.evaluate` true wins and its `then.resolve`
decides; no match falls through to `graph.default_target` (default
`TerminateTarget`, `transitions.py:337-345`).

**Termination primitives.** `TransitionDecision(next_speaker=None)`
terminates (`transitions.py:68-78`); `TerminateTarget(reason="after_work")`
(`:171-178`); runtime intents `Handoff(target, reason)` /
`Finish(summary, reason="finished")` (`ag2/network/handoff.py:25-71`)
short-circuit static rules in `fold` (`workflow.py:246-273`: finish first,
then pre-resolved handoff, then `_select`).

**Turn accounting.** `fold` advances `turn_count + 1` per substantive
envelope (`workflow.py:231-244`); `on_accepted` (`:317-340`) closes when
`expected_next_speaker is None` (graph reason) or `turn_count >=
graph.max_turns` (`auto_close_reason="max_turns"`). Guards close the
channel; they never raise. Stall guards `turn_within(120s,warn)` /
`(600s,auto_close)` (`workflow.py:153-164`) are wall-clock expectations,
not traversal logic.

**Built-in vocabulary.** Targets: agent / round_robin / stay /
revert_to_initiator / terminate (`transitions.py:120-178`). Conditions:
always / from_speaker / tool_called (reads `event_data["routing"]["tool"]`
on `EV_PACKET`, `:206-222`) / context_equals on `state.context_vars`
(`:226-240`). Graphs serialize via `to_dict`/`loads` (`:349-384`);
factories `sequence()` (linear chain, `max_turns=len(steps)`) and
`round_robin()` (`:388-423`).

## 2. Mapping to our builder (B3 order)

| AG2 | `orchestrator/graph.py` |
|---|---|
| `Transition(when,then,priority)` + priority/list order | `add_edge(src,dst,condition=None)`; **registration order is the order** (no priority field in B3); first match wins |
| `Always` | `condition=None` = always-matching; terminates edge evaluation for that node |
| `default_target` (default terminate) | No edge matches at a node → terminate with `exit_reason="condition_met"` |
| `TerminateTarget` / terminal arrival | `set_terminal(keys)`; arrival at a terminal → `exit_reason="terminal_reached"` (checked on arrival, entry included) |
| `max_turns` → `auto_close "max_turns"`, never raises | `max_transitions` cap on edge traversals; trip → structured `TerminationResult(exit_reason="max_transitions")`, never raises |
| `turn_within` wall-clock guards | NOT traversal logic — owned by B4 scheduler via `ctx`. No wall-clock reads in `graph.py` |
| `Handoff`/`Finish` runtime intents | Out of scope B3 (no tools/agents yet); `last_route` passthrough slot reserved for B4 |

**The order's "GraphLoopExceeded result"** maps to the two structured guard
reasons — `loop_guard` (per-node revisit cap) and `max_transitions`
(transition cap) — returned as `TerminationResult`, never raised.

## 3. Intentional divergences (do NOT mirror)

D1. **Per-node revisit cap → `loop_guard`.** AG2 has no per-node revisit
counter (it relies on `max_turns` alone). B3 adds `max_node_visits`
(default 3; entry arrival counts as visit 1; trip when visits would exceed
the cap) because an email-pipeline node re-entered repeatedly signals
oscillation worth distinguishing from plain depth. Reason recorded; Q pins
it like any mirrored semantic.
D2. **No priority field.** AG2 sorts by `(priority, list order)`; B3 uses
registration order only. Equivalent expressiveness for B4's linear-plus-
exception topology; priority may be added later only via ARCHITECTURE.md
amendment.
D3. **Synchronous, envelope-free traversal.** No Hub, no `Envelope`, no
`EV_PACKET`/`EV_TEXT`, no name→id directory, no views, no WAL/hydrate/auth,
no async streams. State is a plain dict threaded through node callables.
D4. **Fail-fast build-time validation** (AG2 validates at channel open in
`validate_create`, `workflow.py:275-291`): unknown `set_entry`/`add_edge`
keys → `ValueError` immediately; missing entry or unreachable terminal →
error at start of `run()` (reachability walk from entry over all edges).

## 4. Fixed semantics C must implement exactly

- Node callable protocol: `fn(state: dict, ctx: dict) -> dict`. Graph passes
  the returned mapping as the next state. Fns must be pure wrt
  `(state, ctx)`; graph never deep-copies and never stores state on `self`.
- `max_transitions=N` allows exactly N edge traversals; the (N+1)th
  attempted traversal is not taken (off-by-one pinned by test). Default
  `max_transitions=100`, `max_node_visits=3` (constructor-overridable).
- `TerminationResult`: `exit_reason` ∈ {terminal_reached, condition_met,
  loop_guard, max_transitions}, `path: list[str]` (every node entered, in
  order), `visits: dict[str,int]`, `transitions_taken: int`,
  `terminated_at_node: str`, `last_route: dict | None = None` (B4 fills
  only from just-observed outcomes).
- Node-fn exceptions propagate unchanged; the graph instance holds no
  per-run state, so it stays clean for the next `run()`.
- Determinism: same graph + same fns + same `(state, ctx)` → same path.
  No `datetime`/wall-clock imports in the module; time comes via `ctx`.

## 5. Parity checklist (Q + A verify at close)

- [ ] P1 First-match-wins in registration order (AG2 `_select` analog,
  `workflow.py:470-479`).
- [ ] P2 Unconditional edge always matches and ends edge evaluation.
- [ ] P3 No-match → `condition_met` termination (AG2 default-target analog).
- [ ] P4 Terminal arrival (incl. entry) → `terminal_reached`.
- [ ] P5 `max_transitions` off-by-one exact (N allowed, N+1 trips, no raise).
- [ ] P6 Revisit cap → `loop_guard` with correct per-node counters (D1).
- [ ] P7 Build-time `ValueError` on unknown node refs; run-start errors on
  missing entry / unreachable terminal (D4).
- [ ] P8 Replay determinism: two runs, identical paths; no wall-clock in module.
- [ ] P9 Instance reuse: no per-run residue on `self`; exception mid-run
  leaves instance clean.
- [ ] P10 `last_route` slot present, default `None`, untouched by traversal.
- [ ] P11 Static scan: no network/credential strings (B2 scan pattern).
- [ ] P12 Linear unconditional chain never loop-guards.
- [ ] P13 Finish short-circuit (A1): node in terminal set executes its fn,
  then terminates `terminal_reached` WITHOUT evaluating any outgoing edge,
  even if edges are registered (registration stays legal). Multi-target
  agent-to-agent Handoff transfer is out of scope (D5).
- [ ] P14 Stall-event auditability (A2): arrivals at a watched node at/above
  threshold append {type: stall_guard, node, count, ts_from_ctx} to the
  result without consuming transition budget or stopping execution; missing
  events = MAJOR (Gate 2).

## 6. Amendments (AGENT-A, 2026-09-27 — append-only, ratified pre-build)

### A1 — Finish mapping decision
Parity §1 cited the `Finish`/`Handoff` short-circuit but left C without a
mirror target. Ruling: mirror the SHORT-CIRCUIT SEMANTICS ONLY. A node in
the terminal set is a finish-node: its fn executes, then edge evaluation
is skipped entirely and `run()` returns `terminal_reached` immediately
(upstream analog: `fold` finish-kind short-circuit,
`adapters/workflow.py:254-265`, and `on_accepted` None-speaker close,
`:327-332`). Registering an outgoing edge FROM a terminal stays legal
(wiring is not validation), but traversal provably never evaluates it —
Q's A1 probe asserts the edge condition fn records zero calls.
D5 (documented divergence, B4 carry): multi-target agent-to-agent Handoff
transfer (name→id resolution, `Handoff.target` routing) is OUT of scope
for B3 — no personas/agents exist until B4. B4 will map handoff targets
onto its agent table; until then no handoff plumbing may be invented.

### A2 — Stall-event records (Gate 2 audit hole closed)
Stall guards are non-traversal by design (§2 mapping), which made trips
invisible to the audit chain. Amendment: `add_stall_guard(node, threshold)`
registers a watcher; each arrival at a watched node with
`visits[node] >= threshold` appends ONE event
`{type: "stall_guard", node: <key>, count: <visits[node]>,
ts_from_ctx: ctx.get("now")}` to `TerminationResult.stall_events`. Events
consume no transition budget and never stop execution (a later `loop_guard`
or terminal outcome still applies, carrying the events with it). Time comes
only from `ctx` — no wall-clock reads in the module. Q extends the
guard-result-audit probe: repeated trips + recovery + budget unaffected +
events present; missing events = MAJOR.

### A3 — Condition-fn exceptions (gap found while specifying Q item 5)
The B3 order asks Q to check "exception-in-condition-fn cleanliness ... per
the parity doc", but no section covered it. Ruling: condition-fn exceptions
propagate UNCHANGED, same as node-fn exceptions (§4) — no catch, no default
branch, no half-traversal. The instance holds no per-run state, so it stays
clean for the next `run()`. C must not invent fallback-branch semantics.
