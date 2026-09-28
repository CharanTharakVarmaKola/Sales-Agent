# B7 Decision Brief — D5 reopen: transfer runtime / executing personas
(AGENT-A, 2026-09-27. ANALYSIS ONLY — no code, no spec, no recommendation.
The scope call is the operator's.)

## The question, stated neutrally
D5 closed twice as INTENT-routing (B4 node, B5 campaign) while TRANSFER —
an agent executing another agent's work — was scoped out for lack of
executing personas. The B7 question: build transfer runtime now, or
consolidate on the five dry channels + intent routing and defer personas?

## What now exists (evidence, not argument)
- Personas-as-data: `agents` table (agent_key, persona_json, model_route)
  holds rows nothing executes — a registry without a runtime.
- Seam precedent: every channel implements paused/transport/usage/route;
  a persona executor would be a third client shape behind the same gate.
- Audit machinery: attempt/action split, idempotency keys, trail channel,
  hash chain — transfer events would ride existing rails, not new ones.
- Live proof so far: transport works, metering lands, chains verify —
  on single-agent paths only. Nothing evidences multi-agent behavior.

## The three known gaps
G1. Stream-per-delegation. AG2 runs each delegation on a fresh MemoryStream
(`subagent_tool.py:47-68`); our `ctx["trail"]` is single-run, not
per-delegation. Scope: small (a scoped trail factory + parent rollup).
Risk if skipped: cross-talk between delegations with no attribution.
Depends on: nothing new — trail pattern already ledgered.
G2. `task_{key}` wrapper. AG2 exposes agents as callable tools with
(objective, context) signatures (`subagent_tool.py:50-74`). Scope: small
(node-shaped wrapper + registry lookup by agent_key). Risk if skipped:
handoff targets stay routing labels, never executable. Depends on: G1
(wrapper needs somewhere to run).
G3. Spend attribution. AG2 leaves mixed-model rollups explicitly unlabelled
(`run_task.py:100-112`) rather than guessing. Scope: medium (per-delegation
usage capture + rollup rule + ledger entry). Risk if skipped: untracked
token growth across delegations. Depends on: G1 (per-delegation streams to
meter) + the funded-model rate card (unpriced today).

## Option 1 — reopen (build transfer runtime in B7)
What it buys: handoff targets become executable; multi-step agent pipelines
become expressible; the `agents` table becomes a runtime, not a registry.
What it costs: G1+G2+G3 plus persona-behavior verification nobody has
specified (the honesty rule: plumbing proofs do not transfer to brains).
Evidence for: seam uniformity (third client shape, no new architecture);
audit rails already fit transfer events. Evidence against: zero live demand
signal yet (no campaign has needed a second agent); spend attribution needs
the still-unpriced rate card.

## Option 2 — consolidate (defer personas, operate the five channels)
What it buys: outreach unlocks proceed on proven single-agent paths;
verification budget concentrates on live sends, deliverability, and the
reply loop — the actual revenue surface. What it costs: handoff stays
routing-only; any future multi-agent need reopens D5 a fourth time (with
this brief as the starting point, not a blank page).
Evidence for: every green finding so far is single-agent; the open blockers
are creds/funding, not architecture. Evidence against: if the first live
campaigns immediately need delegation, consolidation delays it by a batch.

## Decision inputs required from the operator
- Is there a concrete near-term campaign requiring delegation? (Yes → reopen
  has demand; No → consolidation has no cost.)
- Green-light on executing personas as a scope (autonomy posture change).
- Rate-card availability for G3 (else attribution stays unpriced either way).

---

## Appendix A — G1/G2 sizing for real (AGENT-C, 2026-09-27 — estimates, not commitments)

Basis: measured precedents in this repo — `ctx["trail"]` channel ≈ 25 lines
across `nodes.py` (220 total) + runner support; `inbound.py` (122) + 7 tests
(150 lines) for a comparable small module. Ranges assume the same test
standard (fail-closed, dedupe, chain, scans).
- G1 stream-per-delegation (scoped trail factory + parent rollup + lifecycle
  events): 120–180 product lines, 10–14 tests (~200–280 test lines).
- G2 `task_{key}` wrapper (registry lookup by agent_key + (objective,
  context) signature + result shaping): 60–100 product lines, 6–10 tests
  (~120–180 test lines).
"Small" survives: both fit one batch each with the standing test standard,
conditional on R2 (below) staying additive. If schema goes breaking, add a
migration batch.
- Q red-team verdict on "small": PROVISIONALLY ACCEPTED at these ranges —
  the ranges are falsifiable against the precedents cited. Any C build
  exceeding the top of a range without a recorded reason reopens the estimate.

## Appendix B — R2 persona-execution schema survey (AGENT-A, 2026-09-27)

Execution state a persona runtime needs, mapped to storage:
- Run leases (holder, expires): new table `agent_runs` (not a column —
  runs are rows, leases expire independently of the agent definition).
- Budgets (cap, spent tokens, spend): columns on `agent_runs` per run +
  rollup rule (AG2 leaves mixed-model unattributable — adopt that rule).
- Kill flags (per-run halt + per-agent disable): `agent_runs.status`
  (running/cancelled/done) + existing `agents.active` (already a disable flag).
- Delegation edges (parent→child, objective): new table `delegations`
  (parent_run, child_run, objective, result ref).
Verdict: ADDITIVE — two new tables + nullable columns on no existing table
except none required on `agents` (`active` suffices; `budget_cap` optional
nullable later). Zero breaking change to the 10 shipped tables; one Alembic
revision. Non-neutrality guard: this appendix extends evidence only — the
D5 call stays the operator's.

---

## Red-team (AGENT-Q, appended — not merged, 2026-09-27)
R1. "Small" on G1/G2 is asserted without a line-count or test-count basis —
treat both as S/M until C sizes them against the trail precedent. Do not let
"small" become a commitment.
R2. The brief assumes the `agents` table needs no schema change for
execution (persona_json suffices). Unverified: execution may need run-state
columns (leases, budgets, kill flags) — flag as a pre-B7 schema question.
R3. Option 2's "reopens D5 a fourth time" understates process cost mildly —
but the brief itself names this brief as the restart point, which is the
honest mitigation. Acceptable with that sentence present (it is).
R4. No build commitment smuggled: both options are conditional on operator
inputs listed above. Clean.
R5. Missing risk (add, don't argue): EITHER option leaves the reply-handling
loop unbuilt — the brief scopes B7 to transfer-vs-consolidate, but the
operator should know the revenue surface needs the inbound loop regardless.
Not scope creep to name it; would be creep to spec it here.
