# ARCHITECTURE.md — canonical standing rules (AGENT-A owns; append-only)

## Artifact rules (all agents, incl. Q)
CHANGE-NOTES.md is APPEND-ONLY: new batches append a section; never rewrite
or reorder earlier sections. (AGENT-A, 2026-09-27 — ordered at B4 open
after a B3 write-clobber incident that was reconstructed and re-verified.)

## Locked rules

### Gate 2 — SQLite Store (B1, 2026-09-27)
Single-writer `Store` owns every sqlite3 connection (`orchestrator/store.py`).
Per-connection PRAGMAs: WAL, synchronous=NORMAL, busy_timeout=5000,
foreign_keys=ON. Txn shape: BEGIN IMMEDIATE → business SQL + hash-chained
`audit` + `pending_export` outbox → COMMIT (worker-lease txns carry one
audit row per batch/item). Vault export is derived-view only; the export
job is the sole vault writer. Outbox claims use a 600 s lease with
age-based reclaim (`EXPORT_LEASE_SECONDS`).

### Gate 3 — pause-first inference (B2, 2026-09-27)
`InferenceClient` exposes `paused -> (bool, reason)`; `draft()` checks
primary.paused then fallback.paused and raises `InferencePaused(reason)`
before any I/O. Router never swallows `InferencePaused`; transient primary
failure fails over only after re-checking fallback.paused. `last_route` =
{paused, reason, primary, fallback} on every exit. Caller contract: catch
`InferencePaused` by name BEFORE any broad except (B4 nodes).

### Conventions (standing)
No I/O inside fn bodies / pause checks / transition conditions. Single
clock: `orchestrator/timeutil.utcnow/utcnow_iso`. No raw sqlite3 outside
`store.py`. Dry-run-only Phase B; no live client scaffolding until creds
(Gmail/Zoho/AgentMail, OMNIROUTE_API_KEY) land. Personas-as-data live in
the `agents` table, never hardcoded strings.

### Decisions carried
B1 MINORs closed in B2 (shared clock; no-I/O convention documented).
B2 MINOR → B4: nodes write `last_route` to audit only from just-observed
outcomes, never cached values.

## B3 contract — TransitionGraph mirror (AGENT-A, 2026-09-27, pre-build)
Source: `reference/ag2-main` @ `2f9facaf` (transitions/handoff/workflow).
Full contract: `docs/parity-B3.md` + checklist P1–P12.
Locked for C: registration-order first-match-wins; unconditional edge =
always-match terminator; no-match → `condition_met`; terminal arrival →
`terminal_reached`; `max_transitions` default 100 (exact-N off-by-one,
never raises); per-node revisit cap default 3 → `loop_guard` (intentional
D1 addition, AG2 has no equivalent); build-time `ValueError` on unknown
refs, run-start errors on missing entry/unreachable terminal; node protocol
`fn(state, ctx) -> dict`, pure, no wall-clock in module; `TerminationResult`
{exit_reason, path, visits, transitions_taken, terminated_at_node,
last_route=None}; exceptions propagate, instance holds no per-run state.

### B3 amendments (AGENT-A, 2026-09-27, ratified pre-build)
D5: multi-target agent-to-agent Handoff transfer out of scope until B4
(personas); B3 mirrors finish short-circuit only (terminal fn runs, edges
skipped, `terminal_reached`). Stall watchers: `add_stall_guard(node,
threshold)` appends {type, node, count, ts_from_ctx} per qualifying arrival,
no budget consumed, never silent (Gate 2). Condition-fn exceptions
propagate unchanged. Full text: `docs/parity-B3.md` §6.

### B3 close (AGENT-A, 2026-09-27)
PASS, P1–P14 14/14 GREEN, no divergences. Carries into B4: D5 handoff
transfer mapping; B2 MINOR (last_route from just-observed outcomes).
No architecture rule changes at close — 45/45 pytest green.

## B4 contract — nodes + protocol (AGENT-A, 2026-09-27)
Source: `reference/ag2-main` @ `2f9facaf` (ask/turn-loop/envelope/round
capture/delegation). Full contract: `docs/parity-B4.md`, P-B4-1..13.
Rules: pure nodes `fn(state,ctx)->dict`; runner-owned per-run `ctx["trail"]`
audit channel (nodes write-only, conditions never read — the local-stream
analog); R-LASTROUTE-1 (same-frame sources only) + R-LASTROUTE-2 (verbatim
copy, absent → null); catch-by-name at every inference boundary;
quarantine-before-draft; guard/flag outcomes → quarantine_flag rows;
idempotency `sha1(lead|draft_hash|channel)` + OR IGNORE.

### B4 close (AGENT-A, 2026-09-27)
PASS, 13/13 GREEN. B2-MINOR-1 CLOSED; D5 intent CLOSED, transfer re-carried
→ B5. 56/56 pytest green.

## B5 contract — integration dry-run (AGENT-A, 2026-09-27)
Source: `reference/ag2-main` @ `2f9facaf` (post_envelope pipeline,
round capture). Full contract: `docs/parity-B5.md`, P-B5-1..12.
Rules: one entry (traverse → ONE transact → read-back envelope, reads never
mutate); attempts append-only, actions idempotent by key (quarantine
OR IGNORE); D5 intent at campaign scale, transfer-execution SCOPED OUT
until executing personas land.

### B5 close (AGENT-A, 2026-09-27)
PASS, 12/12 GREEN. D5 closed/scoped-out — zero open carries. 65/65 green.
Sole B6 blocker: operator creds.

## B6-PREP contract — live tier skeleton (AGENT-A, 2026-09-27)
Source: novel surface (no vendor mirror); AG2 idempotency precedent
(`envelope.py:111-113` — dedupe at action layer). Full contract:
`docs/parity-B6.md`, P-B6-1..13.
Standing law ledgered: redaction-before-transact on ALL live paths
(CRITICAL — chain is append-only); kill-switch `OUTREACH_LIVE` (uncached
reads, snapshot-per-run, in-flight completes); per-channel activation
(env + scan + smoke); at-least-once + idempotent effects; kind-aware
claiming (kind-blind consumption is an anti-pattern); retro-edit
triple-gate for any B1–B5 touch.
Honesty rule (standing): a local/unfunded model proves plumbing, not
brain — no finding may imply draft-quality or persona-behavior
verification until a funded frontier model runs.

### B6-PREP close (AGENT-A, 2026-09-27)
PASS, 13/13 GREEN. 81/81 green. B6-LIVE waits on creds + staged activation.

## B6-LIVE channel 1/4 — llm/omniroute (AGENT-A, 2026-09-27)
CONDITIONAL PASS, channel dark. Evidence: key valid (`GET /models` 200,
live priced catalog); `POST /chat/completions` → 402 insufficient_balance,
$0.00 spent. Transport + metering match spec; `OMNIROUTE_MODEL` left unset
(operator confirm required). Blockers: wallet funding, model confirm.
Rotation decision: operator's post-smoke. Other channels dark; dry_run holds.

## B6-LIVE channel 2/4 — llm/ollama (AGENT-A, 2026-09-27)
PASS — FIRST FULL UNLOCK (model qwen3:8b, per-invocation per §8 convention).
Keyless by design (no header, pinned); missing-var pause reasons ledgered;
attempts-append (on inference) vs suppression (action layer only) ledgered.
Plumbing, not brain (honesty rule). Omniroute dormant-awaiting-funding.
86/86 green at close.

## STAGE P1+P2 close (AGENT-A, 2026-09-27)
P1 PASS: outreach transports per parity-B6 §10 (SMTP slotted sets, agentmail
base-required + UNVERIFIED path, lease/dedupe contract, connect-phase known-
negative correction in-batch). P2 DELIVERED: `docs/B7-DECISION-BRIEF.md`
neutral + Q red-team R1–R5 appended. 96/96 green. Operator inputs: outreach
creds, omniroute funding, the D5 call.

## Inbound close (AGENT-A, 2026-09-27 — R5 + R2 + sizing)
PASS. Inbound loop per parity-B6 §11 (dedupe/linkage/quarantine/gates, zero
send-side). Brief appended: G1/G2 sizing (measured ranges) + R2 additive
verdict, neutrality guarded. 103/103 green. Only live work remains.

## Rehearsal batch close (AGENT-A, 2026-09-27)
No-new-files discipline in force: evidence as cycle-report notes; appends
only (parity §12, runbook manifests/aborts/checklists, this ledger).
Rehearsals green; rotation-before-smoke is standing law (corrected one
conflicting runbook line pre-violation). Evidence: cycle report.

## STAGE D close (AGENT-A, 2026-09-27)
PASS. Dashboard per parity-B6 �13 prime directive: read-only proven over live HTTP (mutation methods 405/501, zero deltas), hostile-row redaction proven, derivation proven, remote-bind refused, rehearsal fidelity holds. 109/109 green at close. Control room stands; outreach dark, omniroute dormant.

## STAGE D2 close (AGENT-A, 2026-09-27)
PASS. Completion per the DD note: GAPs wired, bot live, staleness + clipboard + a11y in, old pins untouched (channels-dict, terminals alias). 114/114 green.

## STAGE D4 close (AGENT-A, 2026-09-27)
PASS. Separation per order: dashboard/ self-contained, v1 removal verified (nothing beyond the move to delete), hardening tests durable and green, Code Standards ledgered (small functions, no dead code, named constants, docstrings, traceable UI values). 117/117 green.

## STAGE D5 close (AGENT-A, 2026-09-27)
PASS. Launch verified from dashboard/ (all pages + APIs 200); taste T1/T2/T4/T7/T8/T9/T10 applied, personalization rejected by law; skill audit clean; suite + probes green. Server left running for operator review. Standing unchanged.

## UI polish pass close (AGENT-A, 2026-09-27)
PASS. Touch/labels/theads/tooltips/legend per ui-ux-pro-max + ui-styling + design-system.ckm; taste skill ruled out of domain. 117/117 green.

## STAGE D6 close (AGENT-A, 2026-09-27)
PASS. T-item rendered; backend sweep clean (1 unused import removed, 3 guards documented, rest adjudicated). Dashboard in maintenance mode: no redesign batches without new findings or contract change. 117/117 green.

## Agent rotation (operator order, 2026-09-28)
AGENT-DD terminated. AGENT-COCKPIT owns dashboard design authority (instrument-panel doctrine, nine tenets). AGENT-HANDS to be created at build order: implements Cockpit spec exactly, files deviations, preserves the durability suite, read-only + dual-mode laws. Dashboard v2 remains live until v3 replaces it.

## CONTROL ROOM v3 close (2026-09-28)
PASS. Cockpit spec implemented verbatim; honesty holds (NOT-WIRED markers, dormant Instruct, no Test-All). 124/124 green. Dashboard is the operator window; backend follows it next per operator plan.

## CONTROL ROOM v3 HTML rebuild close (2026-09-28)
PASS. Clean-HTML templates + JS renderer + Python JSON transport; spec sections live; honesty holds (NOT-WIRED, dormant Instruct, no Test-All). 124/124 green.

## REBUILD M1 close (2026-09-28)
PASS on branch dashboard-rebuild. Kill-switch real end-to-end (flag persisted, checked pre-dispatch/pre-send, audited). Demo layer live with shape parity. 130/130 green. Waiting operator go-ahead before M2.
