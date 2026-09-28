# Parity contract B5 — end-to-end integration dry-run (AGENT-A, pre-build)

Source: `reference/ag2-main` @ `2f9facaf` (read-only). Composition grounding:
`Hub.post_envelope` (`ag2/network/hub/core.py:1853`, `:2003-2034`) = dispatch
→ read-only listener fan-out (`:2015-2019` "read-only state-transition
notification") → conditional channel transition with `auto_close_reason`
(`:2027-2032`); envelope spans close even on escaped exceptions
(`:2008-2013`). Our analog: traverse → persist atomically → read-back
envelope (reads never mutate) → return. Round capture stays
`build_round_envelope` one-ask-round-one-packet (`adapters/workflow.py:374-417`).

## 1. Composition contract (binding on C)

`run_lead_campaign(store, *, lead_row_id, lead, graph, inference, run_id,
actor)` does exactly: build `ctx` {now, run_id, inference} → B4
`run_and_persist` (the ONE transact per run — no new transaction semantics
at this layer) → read back the audit row + pending outbox row → assemble
the envelope → return. Reads after COMMIT never mutate. Node/graph/store
modules unchanged except §3.

Result envelope (exact fields): {run_id, lead_row_id, exit_reason, path,
terminated_at_node, quarantined: bool, paused: bool, pause_reason: str|None,
last_route: dict|None (verbatim or null), stall_events: list (byte-identical
to graph result), audit_id: int, outbox_id: int, chain_ok: bool}.

## 2. Attempt-vs-action semantics (binding; resolves the duplicate probe)

Traversal ATTEMPTS are append-only events: two invocations = two audit rows
(distinct attempts, both exported). ACTION rows (anything that could one day
cause a send) are idempotent by `idempotency_key`: retrying the same run_id
must not duplicate them. Narrow C change authorized here: the two
quarantine inserts in `run_and_persist` become INSERT OR IGNORE (same
deterministic keys; single-run behavior identical; Rule 7 extended to
quarantine rows). Q's headline probe: same run_id twice → exactly ONE
quarantine action row AND a chain that verifies; the two audit rows both
exist (attempts, not duplicates).

## 3. D5 verdict — SCOPE-OUT of transfer-execution (no fourth carry)

Rationale: transfer-execution means an agent executing another agent's work
(`as_tool`/subtask surface, `agent.py:1552`, `:1477`). B5 runs dry with no
executing personas — there is nothing to transfer TO. Inventing a transfer
runtime now would be speculative generality against dry_run posture.
CLOSED in B5: handoff INTENT at campaign scale — a campaign graph that
routes draft → `on_handoff` lanes → terminals end-to-end with audited
reasons (test). SCOPED OUT (ledgered, reopens only when executing personas
land, earliest B7): name→agent resolution against the `agents` table and
cross-agent work transfer. D5 is hereby closed as a carry.

## 4. Checklist P-B5-1..12 (Q verifies; A closes)

- [ ] P-B5-1 One entry composes graph+nodes+Store; exactly one transact/run.
- [ ] P-B5-2 Full dry-run path: lead → draft node → pause → paused-terminal
  → audit+outbox paused-record → envelope (all fields §1).
- [ ] P-B5-3 Double-invocation: one quarantine action row, two attempt
  audits, chain verifies (§2).
- [ ] P-B5-4 Guard mid-campaign → quarantine record + full chain verify.
- [ ] P-B5-5 Seam matrix: raise at node-entry / post-inference /
  post-persist / pre-return with specified row deltas (0/0/0/committed).
- [ ] P-B5-6 A2 byte-identity: envelope.stall_events == graph result ==
  audit payload, all three layers.
- [ ] P-B5-7 D5 campaign-scale intent routing + audited reasons; transfer
  scope-out recorded (no transfer plumbing in code — grep-assert).
- [ ] P-B5-8 Regression: B1–B4 suites untouched and green; no pin weakened.
- [ ] P-B5-9 Static scans (network/creds/sqlite3/datetime) on new code.
- [ ] P-B5-10 stdlib-only; no I/O in node/graph fns; dry_run posture.
- [ ] P-B5-11 Append-only artifacts (CHANGE-NOTES appended, ledger appended).
- [ ] P-B5-12 Reads-after-COMMIT mutate nothing (envelope assembly provably
  read-only: second assembly returns equal envelope, zero new rows).

## 5. Q probe queue
Independent seam-matrix re-runs with own injection points; headline
duplicate-send hunt (§2); A2 triple-layer byte comparison; regression
diff-check (B1–B4 test files byte-identical to B4 close); transfer-plumbing
grep (`as_tool`, `subtask`, `passport`, `delegate` absent from product code).
