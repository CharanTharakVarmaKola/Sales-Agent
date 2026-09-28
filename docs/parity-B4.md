# Parity contract B4 — nodes + node protocol (AGENT-A, pre-build)

Source: `reference/ag2-main` @ `2f9facaf` (read-only; re-read for B4).
AGENT-C builds `orchestrator/nodes.py` (+ runner) to this contract.
Checklist P-B4-1..13 pre-committed; Q verifies each independently.

## 1. What AG2 actually does (file:line)

**Agent invocation.** `Agent.ask(*msg, stream, dependencies, variables,
prompt, config, tools, middleware, observers, response_schema, hitl_hook)`
(`ag2/agent.py:971-984`) is "the blocking degenerate case of `run`: open a
run, drive it to its result" (`:985-1000`). One ask-round = one unit of
agent work with explicit inputs and a typed reply (`AgentReply`).

**Turn loop.** `_execute_turn` (`agent.py:1629-1668`): send event → await
`ModelResponse` → while `tool_calls` present (and not `response_force`),
execute tools and re-query; drain leftover enqueued input; return the final
message. Loop continues ONLY on model-emitted tool calls; termination is
model-driven per turn, graph-driven per channel.

**Wire shape.** `Envelope` (`ag2/network/envelope.py:95-128`): channel_id,
sender_id, audience (None = broadcast), event_type, event_data + stamped
envelope_id/created_at. `EV_PACKET` event_data =
{routing:{kind,tool?,reason?,target?}, context_updates, body} (`:45-61`).
`visible_to` is a pure delivery predicate (`:150-160`).

**Round capture.** `WorkflowAdapter.build_round_envelope`
(`adapters/workflow.py:374-417`) captures one `Agent.ask` round atomically
into an `EV_PACKET`; `extract_turn_input` (`:362-372`) decodes the inbound
envelope into the next speaker's prompt; `fold` (`:194-273`) advances
bookkeeping; `validate_send` (`:293-315`) enforces speaker order. Agent
results become next state ONLY through the envelope → fold path — never by
shared mutation.

**Delegation.** `Agent.as_tool` (`agent.py:1552`) wraps an agent as
`task_{name}(objective, context)`; subtasks run on their own `MemoryStream`
with no recursive delegation (structural, per AGENTS.md + `:1477`).

## 2. Mapping to our B4 surface

| AG2 | B4 (`orchestrator/nodes.py` + runner) |
|---|---|
| `ask(*msg, ...)` one round, explicit I/O | Node `fn(state, ctx) -> dict`: state in, new state out. Nodes are PURE (no I/O, clock via `ctx["now"]`); ALL persistence lives in the runner's `Store.transact` |
| turn loop on tool_calls | No tool loop in B4 (no tools yet). Draft node makes exactly ONE inference call per visit |
| envelope → fold → next state | Graph threads the returned mapping as next state; runner persists the `TerminationResult` + outcome slots after `run()` returns |
| `validate_send` speaker order | Graph edge conditions (incl. handoff intent, D5) decide routing; runner never overrides traversal |
| `as_tool` / subtasks | Out of scope (no personas execute until B5+). D5 closes the *intent* half only — see §4 |
| `visible_to` / audience | Out of scope (single-graph B4). Quarantine visibility rule instead: quarantined content never enters an inference call (§5) |

**Node set (dry-run B4):** `draft_node` (B2 client, pause path),
`quarantine_check` (flags state, never sends), `handoff_node` (D5 intent),
`run_and_persist` runner (graph.run → ONE `transact`: audit + outbox +
action rows). Idempotency helper `idempotency_key(lead_id, draft_hash,
channel) = sha1(...)` (Rule 7; INSERT OR IGNORE in runner).

## 3. LAST_ROUTE exact rule (closes B2 MINOR — binding, not guidance)

R-LASTROUTE-1: `state["last_route"]` (or any audit payload route field) may
be assigned ONLY from values produced inside the same call frame: (a) the
return of a `client.draft()` call just made; (b) `client.last_route` read
immediately after a `client.draft()` call just made on that same object; or
(c) a record built inside an `except InferencePaused` frame from `e.reason`
+ that client's `name`. Any other source — prior runs, seeded state,
cached variables across nodes — is a STALE WRITE (MAJOR).
R-LASTROUTE-2: the runner copies `state["last_route"]` into the audit
payload verbatim (absent → null). It never synthesizes, merges, or defaults.
Q pins R-1 with the forgery probe: seed a forged `last_route`, run the
paused path, assert the audit carries the fresh pause record.

## 4. D5 closure (partial-implement + formal re-carry)

IMPLEMENTED in B4: handoff intent as routing data. `handoff_node` records
`state["handoff"] = {"target": <node_key>, "reason": <str>}` (set by the
visiting decision; in dry-run B4 the intent arrives in state); edge
condition factory `on_handoff(target)` matches it; the runner audits
(target, reason) with the traversal outcome. `Finish` stays terminal
arrival (B3). RE-CARRIED to B5: cross-agent transfer (name→agent resolution
against the `agents` table, persona execution, `as_tool`-style wrapping) —
no agent executes another agent's work in B4, and no such plumbing may be
invented here.

## 5. Boundary rules (binding)

- Catch-by-name: every node that touches inference wraps the call in
  `except InferencePaused` FIRST; the pause routes to the paused-terminal
  path with `pause_reason` + fresh `last_route`. Broad `except` may only
  follow it and must re-raise or quarantine — never swallow.
- Quarantine-before-draft: `draft_node` checks quarantine flags FIRST and
  returns without calling inference (call count stays zero).
- Guard outcomes: `loop_guard`/`max_transitions` → runner writes a
  `quarantine_flag` action row (GraphLoopExceeded is a quarantine record,
  never a silent drop). Stall events persist inside the same transact.
- Crash-mid-pause: a raise between pause-catch and audit-write must leave
  zero audit/outbox rows (B1 txn shape holds at the new seam).

## 6. Checklist P-B4-1..13 (Q verifies each; A closes)

- [ ] P-B4-1 Node protocol `fn(state,ctx)->dict`, purity (no I/O, ctx clock).
- [ ] P-B4-2 Draft node keeps pause-before-network; dry_run raises, no
  creds/network strings.
- [ ] P-B4-3 Catch-by-name at every inference boundary; pause → paused path
  with reason, never swallowed.
- [ ] P-B4-4 R-LASTROUTE-1/2 incl. forgery probe (fresh record wins).
- [ ] P-B4-5 Every traversal outcome → ONE `Store.transact` (audit+outbox).
- [ ] P-B4-6 Guard outcomes → `quarantine_flag` action rows, never silent.
- [ ] P-B4-7 Stall events round-trip through transact into audit payload.
- [ ] P-B4-8 D5 intent edges + audited reason; multi-agent re-carry recorded.
- [ ] P-B4-9 Idempotency key exact `sha1(lead|draft_hash|channel)` +
  INSERT OR IGNORE dedupe pinned.
- [ ] P-B4-10 Static scans (network/creds/sqlite3/datetime) + stdlib-only +
  dry_run posture.
- [ ] P-B4-11 Store seam: runner-only writes; two concurrent runs on one
  Store stay consistent (RLock).
- [ ] P-B4-12 Crash-mid-pause leaves zero partial rows.
- [ ] P-B4-13 Quarantined state never reaches inference (call count zero).

## 7. Q probe queue (from A)
Forgery-last_route, crash-mid-pause, stall+terminate same-run chain
integrity, two-run Store race, quarantine-bypass attempt (quarantined lead
into draft node → zero calls + quarantine outcome), raising-condition
cleanliness at node-graph seam, `on_handoff` mismatch (no match →
`condition_met`, not hang).
