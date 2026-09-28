# CHANGE-NOTES — Batch B1 (Store + initial migration)

## Implemented
- `orchestrator/__init__.py` — Phase-B package marker.
- `orchestrator/store.py` — single `Store` class (Gate 2 concurrency policy,
  Decision 8 schema). Per-connection PRAGMAs (WAL/NORMAL/busy_timeout 5000/
  foreign_keys ON); `threading.RLock` single-writer; `transact()` =
  BEGIN IMMEDIATE → fn(conn) → audit (hash-chained) + outbox → COMMIT. Outbox
  worker primitives `claim_exports()` (time-lease reclaim, 600 s) and
  `mark_export_done()` keep the same txn+audit shape.
  `fetch_for_llm()` refuses quarantined rows (`QuarantinedContentError`).
  FK enforcement verified at runtime (probe: ghost lead → IntegrityError).
- `migrations/versions/0001_initial.py` — Alembic revision `0001_initial`
  (10 tables) with the 4 review fixes: composite `uq_leads_customer_email`,
  `quarantine_reason` + enforcement note, audit `prev_hash/row_hash`,
  sweep indexes (`idx_leads_next_action` partial, `idx_actions_status_lease`,
  `idx_messages_provider_msg` partial unique).
- `tests/test_store.py` — pytest, dry-run only (tmp SQLite, no
  creds/network/vendor): PRAGMAs, atomic txn shape, rollback cleanliness,
  hash-chain + tamper detection, idempotency UNIQUE, composite UNIQUE,
  COMMIT-then-crash outbox survival, quarantine guard, FK restrict,
  worker-txn audit shape, claim-lease reclaim, indexes, no-raw-sqlite3,
  migration static coverage.

## Decisions covered
Gate 2 (concurrency + resume primitives), Decision 8 (schema, 4 fixes
folded in), Rule 8 (SQLite truth; vault export job consumes `pending_export`
in a later batch).

## Deviations (with reason)
- `Store` carries its own `SCHEMA_DDL` mirror of the Alembic migration so
  tests/boot work with zero alembic-env dependency. Migration file is
  canonical for `alembic upgrade`; mirror is byte-checked by
  `test_migration_covers_ten_tables_and_fixes`. Justification: B1 has no
  alembic.ini/env.py yet; env arrives with Phase-B service wiring.
- Worker-lease txns (`claim_exports`/`mark_export_done`) write audit rows
  (one per batch/item) to keep the standing txn shape. Cost: extra audit
  rows per export cycle. Accepted: keeps hash-chain complete, no silent
  mutations. No constraint conflict.
- `threading.RLock` instead of `Lock` (re-entrancy guard; single-writer
  discipline unchanged — transaction bodies must still use the passed conn).

## Parallel actions
- Archived `genspark-build01/` → `archive/genspark-build01-archive.zip`
  (34 entries, 74441 bytes; verified missing reply_classifier/run_loop =
  stale build) then deleted per Decision 7 (archive-first).
- Creds queue (Gmail/Zoho/AgentMail, OMNIROUTE_API_KEY): NOT landed —
  B6/live-LLM tier stays dry_run + paused. Operator action open.

---

# Batch B2 (inference layer + shared clock)

## Implemented
- `orchestrator/inference/base.py` — `InferenceClient` ABC (`draft` +
  `paused` abstract property) + `InferencePaused(reason)` with `.reason`
  for audit; B4 caller contract documented (catch by name before broad
  except). Gate 3.
- `orchestrator/inference/dry_run.py` — `DryRunInferenceClient`: always
  `(True, "dry_run: awaiting creds")`, `draft()` always raises. Zero
  network code paths by construction.
- `orchestrator/inference/routing.py` — `RoutingInferenceClient`:
  primary.paused → fallback.paused → raise-before-I/O; transient primary
  failure fails over only after re-checking fallback.paused; all three
  draft() call sites record `last_route` and re-raise InferencePaused
  (incl. pause-race legs). `last_route` =
  {"paused","reason","primary","fallback"} on every exit.
- `orchestrator/timeutil.py` — `utcnow()` + `utcnow_iso()`; Store migrated
  onto it (carried MINOR 2 closed). Module docstrings record the no-I/O
  convention (carried MINOR 1).
- `tests/test_inference.py` — pause semantics, route recording, no-swallow
  through generic handler, transient failover + paused-fallback never-called,
  audit round-trip via Store, no-network/no-credential static scans,
  single-clock scan, pause-race + crash-mid-draft probes.

## Decisions covered
Gate 3 (pause-first interface). No live clients scaffolded per order.

## Deviations
- None from the B2 order. One Q fix-round change inside the batch:
  pause-race legs now record `last_route` before re-raising (audit
  completeness); no spec conflict.

## Parallel actions
- Archive moved `Temp\opencode\genspark-build01-archive.zip` →
  `archive/genspark-build01-archive.zip` (74,441 bytes).

---

# Batch B3 (graph mirror)

## Implemented (AGENT-C, to docs/parity-B3.md as amended — spec won, no conflicts found)
- `orchestrator/graph.py` — `TransitionGraph` (stdlib only): `add_node(key,
  fn=None)`, `add_edge(src,dst,condition=None)` (unknown refs → GraphError
  immediately), `set_entry`, `set_terminal`, `add_stall_guard(node,
  threshold)`; `run(state,ctx)` = entry → registration-order first-match →
  route/terminate. Guards never raise: `max_transitions=100` (exact-N),
  `max_node_visits=3` (D1) → structured results; tripping arrivals are
  appended to `path` before the guard return (audit completeness).
  Terminals are finish-nodes (A1/D5): fn runs, edges skipped unevaluated.
  Stall watchers (A2) append {type,node,count,ts_from_ctx} per qualifying
  arrival, zero budget cost. Node- AND condition-fn exceptions propagate
  unchanged (A3). No datetime/wall-clock in module; per-run state local.
- `tests/test_graph.py` — 18 tests: cycle+counters, off-by-one both
  directions, first-match, unconditional terminator, condition_met,
  terminal/entry-terminal, finish-edge-dead (A1), constructor-vs-firstrun
  validation boundary, replay determinism, shared-state visibility,
  instance reuse, linear-never-guards, stall-record+budget (A2),
  guard-audit fields, node/condition exception propagation (A3),
  last_route-None, static scans.
- 45/45 pytest green (14 B1 + 13 B2 + 18 B3).

## Decisions covered
Parity P1–P14, ARCHITECTURE.md B3 contract, D1–D5, A1/A2/A3.

## Deviations
- None. One judgment call recorded (not a deviation): guard-tripping
  arrivals are included in `path` (spec: "path: every node entered" —
  arrival happened; fn correctly not executed on over-cap arrival).

---

# Batch B4 (nodes + node protocol)

## Implemented (AGENT-C, to docs/parity-B4.md — no spec conflicts found)
- `orchestrator/nodes.py` — `draft_node` (quarantine-first, one inference
  call/visit, catch-by-name → paused path + same-frame fresh last_route),
  `quarantine_check` (pure flag), `handoff_node` + `on_handoff` (D5 intent),
  `on_paused`/`on_quarantined` factories, `idempotency_key` (Rule 7),
  `run_and_persist` (fresh per-run `ctx["trail"]` audit channel; ONE
  transact: audit+outbox+quarantine/guard rows; node-raise persists
  nothing), `persist_draft_action` (INSERT OR IGNORE).
- `tests/test_nodes.py` — 11 tests: purity, paused e2e, forgery,
  quarantine-zero-calls, guard→quarantine, stall round-trip, idempotency,
  D5 route/mismatch, crash-cleanliness, 4-thread race, static scans.
- 56/56 pytest green (14+13+18+11).

## Decisions covered
Parity P-B4-1..13; B2-MINOR-1 CLOSED (R-LASTROUTE-1/2); D5 intent CLOSED,
transfer re-carried → B5.

## Deviations
- None from spec. Seam fix S1 (in Q findings): runner passes integer
  lead_row_id/0 as outbox entity_id (audit keeps string run_id) —
  spec-compatible (parity never typed the outbox entity), B1 untouched.
- Mechanism note: `ctx["trail"]` runner-owned audit channel (nodes
  write-only, conditions never read) — specified here and in module
  docstring for A ledger review.

---

# Batch B5 (integration dry-run)

## Implemented (AGENT-C, to docs/parity-B5.md — no spec conflicts found)
- `orchestrator/campaign.py` — `run_lead_campaign` (ctx → ONE B4 transact
  → read-back `CampaignEnvelope` {run_id, lead_row_id, exit_reason, path,
  terminated_at_node, quarantined, paused, pause_reason, last_route,
  stall_events, audit_id, outbox_id, chain_ok}) + `assemble_envelope`
  (pure-read; re-assembly equal, zero new rows).
- Narrow authorized change (parity §2): quarantine inserts in
  `run_and_persist` → INSERT OR IGNORE (attempts append, actions dedupe).
- `tests/test_campaign.py` — 9 tests: full path, double-invocation,
  guard+chain, 4-seam matrix, A2 triple identity, D5 intent e2e,
  transfer-plumbing grep, static scans.
- 65/65 pytest green (56 prior untouched + 9 new).

## Decisions covered
Parity P-B5-1..12; D5 CLOSED (intent at campaign scale; transfer-execution
scoped out per parity §3 — reopens only with executing personas).

## Deviations
- None.

---

# Batch B6-PREP (live tier skeleton — no creds, no network)

## Implemented (AGENT-C, to docs/parity-B6.md — one spec correction noted)
- `orchestrator/posture.py` — `OUTREACH_LIVE` flag, uncached `is_live()`,
  per-run `posture_snapshot()`, per-channel `channel_status()` (booleans
  only), 4-slot Zoho fleet (`_05` deprecated in `DEPRECATED_ENV`, never read).
- `orchestrator/redact.py` — `redact_for_audit()` (keyed secrets, bearer,
  private-key blocks, sk/am_ prefixes). Pure, no I/O.
- `orchestrator/live.py` — pause-first `LiveChannelClient`
  (OmniRoute/FreeLLM) + `LiveEmailSender` (Gmail/Zoho/AgentMail); env read
  at call time, nothing cached/stored; `_transport()` seam raises
  `LiveNotConfigured` (real transports = B6-LIVE per channel post-smoke).
- `orchestrator/consumer.py` — at-least-once skeleton: claim → send →
  done+audited; transient/paused → lease-held + audited; exhausted →
  dead-lettered + audited. All payloads redacted. Owns `kinds` selection.
- `tests/test_prep.py` — 16 tests, mocked env (monkeypatch, fake values) +
  stubbed transports. Zero network.
- 81/81 pytest green (65 prior + 16 new).

## Decisions covered
Parity P-B6-1..13; redaction rule PINNED; kill-switch + activation specced.

## Deviations
- Spec correction (dead-letter): pre-build draft specified a failed
  `actions` row; corrected to audit-only death (generic exports lack a
  lead anchor; `actions.lead_id` is FK-bound) — parity §2 amended same batch.
- Triple-gated retro-edit: `claim_exports(kinds=None)` filter (default =
  B1 behavior). Necessity: consumer/producer exhaust feedback loop found
  in-batch. B1 pins re-run green. This entry is gate (3).

---

# STAGE P1 (outreach transports, dry/fake)

## Implemented (AGENT-C, to parity-B6 §10 — one correction in-batch)
- `SmtpSender` + `GmailSmtpSender` + `ZohoSmtpSender(slot 01–04)` (live.py):
  one SMTP impl, slotted credential sets; connect-phase failures are
  known-negative `SmtpTransient`, send-phase network failures are
  `SendUnknown` (reconcile-by-key, never blind-resend); auth/no-recipient
  fail hard; `poll_inbox` minimal IMAP shape; metering sans secrets.
- `AgentMailApiSender`: base REQUIRED, path default `/messages`
  UNVERIFIED_PENDING_LIVE_DOCS (live-doc gate recorded in runbook).
- `resolve_unknown(store, key)` (actions-table hinge) + `lease_window_s()`
  (OUTREACH_LEASE_SECONDS, default 600); missing-var pause reasons.
- `tests/test_transport.py` — 10 tests vs in-process fake SMTP/IMAP
  (record/latency/failure injection): fail-closed ×3, lease-dedupe,
  honest retry, hard-fail→consumer-dead-letter, IMAP shape, slot isolation,
  agentmail shapes, L1 vocabulary, metering secret-freedom.
- 96/96 pytest green (86 prior + 10 new).

## Decisions covered
Parity §10 T1–T4; zero B1–B5 touches (CHANNELS append + sender `__init__`
are B6 files).

## Deviations
- Correction (not a deviation): connect-phase OSError reclassified from
  SendUnknown to SmtpTransient (nothing sent = known outcome) — spec §10
  upheld, test updated to the corrected semantics.

---

# STAGE P2 (B7 decision brief — analysis only, no code)

## Delivered
- `docs/B7-DECISION-BRIEF.md` (AGENT-A): neutral D5-reopen brief — evidence,
  gaps G1–G3 with scope/risk/dependencies, both options argued, operator
  inputs listed. No recommendation, per order.
- Q red-team R1–R5 appended to the brief (not merged): G-size skepticism,
  possible run-state schema need, inbound-loop naming.
- AGENT-C stood down. No construction, no spec issuance beyond the brief.

---

# Batch inbound (R5 + R2 + G-sizing)

## Implemented (AGENT-C, to parity-B6 §11)
- `orchestrator/inbound.py` — `poll` (contract-checked fetcher) +
  `reconcile` (one batch txn): distinct-keyspace dedupe (OR IGNORE),
  exact-match linkage (unknown → new thread, never merged), quarantine-by-
  default, per-message audit outcomes, deterministic unsubscribe/bounce
  gates → stage closed/paused, malformed (incl. non-dict) loud-reject.
- `tests/test_inbound.py` — 7 tests: fail-closed, dedupe, linkage/spoof,
  terminal gates, malformed, no-outbound-effect (grep + behavior), chain.
- 103/103 pytest green (96 prior + 7 new).

## Decisions covered
Parity §11; R2 additive verdict + G1/G2 measured ranges appended to brief
(estimates, not commitments); Q red-team survived both.

## Deviations
- One in-batch spec-alignment fix: non-dict envelopes rejected (not raised)
  — §11's "malformed never dropped" required it; fail-closed preserved.

# STAGE D (operator dashboard — read-only prime directive)

## Implemented (AGENT-C, to parity-B6 §13)
- `orchestrator/dashboard.py` — stdlib-only control room: pure-read views
  (machine/ledger/compliance/queue) over Store reads + posture + live file
  scans; every view redacted pre-render; GET-only (others 405, unknown 404
  without reflection); localhost bind unless ALLOW_REMOTE=1; error paths
  generic. No INSERT/UPDATE/DELETE strings in module; `.env` never opened.
- `tests/test_dashboard.py` — 6 tests over live HTTP: read-only proof
  (all routes + 5 mutation methods, zero row deltas), hostile-row redaction
  proof, derivation proof vs direct queries, remote-bind refusal, gmail
  fake-walk rehearsal fidelity.
- 109/109 pytest green (103 prior + 6 new).

## Decisions covered
Parity §13 prime directive; zero B1–B5 touches; zero transport-seam touches.

## Deviations
- None.

---

# Rehearsal batch (activation dry-walks, no-new-files discipline)

## Notes (AGENT-A/C/Q — evidence lives in the cycle report, not files)
- A: parity-B6 §12 rehearsal-scope law (rotation-before-smoke, abort-branch
  naming, notes-not-files). State-of-the-Machine in the cycle report.
- C: 14/14 session steps green vs fakes (gmail/zoho/agentmail/omniroute);
  env manifests appended to runbook; slot isolation + pause re-darkening proven.
- Q: rotation-order conflict found and corrected to §12 (no violation had
  occurred); abort clauses + test-recipient gap appended to runbook.
- 103/103 green. Zero files created this batch.

---

# Batch B6-LIVE channel 1/4 (llm/omniroute — CONDITIONAL, channel dark)

## Implemented (AGENT-C)
- `OmniRouteClient._transport` (live.py): OpenAI-compatible POST, env
  base/key/model at call time, fail-closed on unset model, contract-checked
  returns, `last_usage` metering (model/tokens, cost unpriced — no rate
  card invented). stdlib urllib only.
- `draft_node` metering trail entry (triple-gated B4 touch: necessity =
  per-call spend ledger; suite green; this entry is gate (3)).
- `scripts/live_smoke_omniroute.py`: fail-closed smoke (flag+key+model),
  redacted spend audit row, chain verify, key never printed. Not in pytest.
- Static scan evolved: live.py alone may use urllib (test pins allowlist).

## Evidence (AGENT-Q, live)
- `GET /models` → 200, priced catalog (key valid, auth correct).
- Smoke `POST /chat/completions` → 402 insufficient_balance, $0.00 spent.
- S2 adapted PASS (stubbed). L1 scans PASS. L2 BLOCKED-wallet; L3–L5 pending.
- Operator actions: fund wallet → confirm model (`gpt-5-nano` recommended,
  ~$0.00002) → Q re-runs L2→L4 → rotation decision.

## Deviations
- None from parity-B6 (first network code shipped exactly at the specced seam).

---

# Batch B6-LIVE channel 2/4 (llm/ollama — PASS, FIRST FULL UNLOCK)

## Implemented (AGENT-C, same seam)
- `OllamaClient` (live.py): native `/api/chat`, explicit no-auth (no header
  ever built, pinned), fail-closed base+model, contract-checked returns,
  `last_usage` with cost 0.0/local-inference.
- Missing-var pause reasons (all live adapters): names, never values.
- `scripts/live_smoke_ollama.py` (not in pytest) + 4 suite tests (fail-closed
  shapes, no-header proof, metering). No B1–B5 touches.
- 86/86 pytest green (82 prior + 4 new).

## Evidence (AGENT-Q, live, model qwen3:8b)
- S1: real call, 65/276/341 tokens, $0.00, audit_id=1, chain verifies.
- S2 live: crash→zero rows, retry→honest second row, chain verifies.
  Order-line conflict resolved: attempts append (ruling stands), suppression
  lives only at the action layer.
- L2–L4 green; `channel.unlocked` recorded (3-row verified chain).
- `.env`: OLLAMA_BASE_URL + OLLAMA_MODEL=qwen3:8b pinned (non-secret config).

## Deviations
- None.

---

# STAGE D2 (dashboard completion � DD spec, build, verify)

## Implemented (AGENT-C, to the DD note in the cycle report)
- Wired all GAPs: per-channel paused_reason, smoke ts+audit_id, outbox depth/in-flight/by-kind, quarantine action rows, queued counts, intents/threads/messages counts, agents registry (key/role/active only), chain head, spend totals, ledger ?event=/?limit=, as_of/head/stale per view, bot Q&A, copy buttons, dark CSS + landmarks + keyboard (r, /, Esc).
- Compat kept: channels dict, channel_order ordering, terminals alias. 114/114 pytest green (103 prior + 11 new).

## Deviations
- None from the DD note. Probe-side correction (Q closed): zoho dark until all 4 slots set (strict fleet posture).

---

# STAGE D4 (dashboard separation + hygiene)

## Structure (AGENT-C)
- File map: `orchestrator/dashboard.py` → `dashboard/app.py`;
  `orchestrator/static/style.css` → `dashboard/static/style.css`;
  `tests/test_dashboard.py` → `dashboard/test_dashboard.py`;
  new `dashboard/__init__.py` (single entry: `Dashboard, serve`).
  Pure relocation + import-path updates; zero logic changes.
- ORDER 2 removal proof: `orchestrator/dashboard.py` no longer exists;
  no imports/routes reference the old path (suite green proves it).
  v1 was already fully replaced by the D2 rewrite — no separate v1 file
  ever coexisted; nothing further to delete.
- Incidents in-batch: two mojibake/docstring-closure syntax breaks from
  shell round-trips (fixed, ASCII-only docstrings now); one stale
  `orchestrator.dashboard` import fixed. All caught at collection.

## Q hardening (durable tests, KEPT — no temp scripts written)
- Wiring harness (every value traced to source), dummy detector
  (reference-content hunt), route inventory (map/nav/tests triple match),
  flip fixtures (`seed_demo_ledger` shared).
- Dummy-hunt adjudication: numeral shapes like 12 are legitimate derived
  counts (9 dark + funding + D5 + recipient) — detector hunts reference
  content; flip-behavior proves the numerals.

## DD Code Standards note (binding on C henceforth)
Small single-purpose functions; no dead code or commented-out code;
explicit error handling (no silent excepts); named constants, never magic
numbers; one-line responsibility statements; docstrings on public functions;
imports used or removed; formatting consistent; every UI value traces to a
named source function. C self-review applied: dead CSS rules removed,
display budgets named, public docstrings added.
- 117/117 green at D4 close (103 + 14 dashboard).

---

# STAGE D5 (dashboard launch + taste refinement)

## Taste items (AGENT-DD directed, AGENT-C executed — accepted)
- T1 tabular numerals (.kpi/.mono); T2 8/16/24 spacing scale via vars;
- T4 thead headers on tables; T7 capitalized chips; T8 hero spans 2 cols;
- T9 hover states; T10 absolute+relative timestamps (agents, smokes).
- Rejected: greeting personalization (underivable operator identity — law wins).
## Skill audit: CSS vars for colors/spacing/radii; dead rules removed;
- naming kebab-consistent; every class used.
## Verification (AGENT-Q): suite green; flip/redaction/dummy coverage holds
- over refined markup; suite-transport flake tamed with a resilient test
- fixture (assertions untouched — 117 green x3 consecutive).
- 117/117 green at D5 close.

---

# UI polish pass (skills-driven, 2026-09-27)

## Implemented (DD directed per ui-ux-pro-max + ui-styling + design-system.ckm)
- Touch targets 40px+ with cursor + press feedback; visible ask labels with
  examples; scope=col on all theads; bar title tooltips + visible legend;
  hero aria-label; empty-state and stale behavior unchanged.
- `taste` skill ruled OUT (music-video/fancam domain, not SaaS console) —
  recorded so the question never recurs.
- Q probes green (tooltip absence on empty db is correct empty behavior).
- 117/117 green; zero new files.

## Deviations
- None.

---

# STAGE D6 (audit-lock + unblock round)

## Implemented
- T-item: zoho per-slot reasons render as human lines (slot: reason or ok), monospace cell.
- Sweep fixes (trivial): removed unused asdict import (campaign.py); rollback-best-effort comments on all three txn guards (store.py). False positives adjudicated: TEMP-substring markers, redact hunting-patterns, intentional fallbacks.

## Deviations
- None. Dashboard enters maintenance mode per order.

---

# CONTROL ROOM v3 (Cockpit spec, AGENT-HANDS build)

## Implemented (exactly against the Cockpit note)
- 11-section nav (question-driven); Email 17 verbatim with LIVE/WIRED-IDLE/NOT-WIRED derivation; Providers allowlist cards + count chips; Agent Queue vs Agents registry; Leads board + search/filter/drill-down; dual-mode Ask wired + Instruct dormant-disabled; kill readout-with-reason; light+dark first-class themes with toggle; theads/legends/empty states throughout.
- Suite: v2 durability preserved and extended (Email-17, providers, queue, CRM, dual-mode, themes). 124/124 green.

## Deviations
- None. One spec-side note: 'Test All' action excluded per read-only law (spec �0 anticipated this).

---

# CONTROL ROOM v3 HTML rebuild (operator order)

## Implemented (AGENT-HANDS, against the Cockpit note)
- dashboard/templates/: 12 hand-written HTML files (shell + data-bind regions, no values in markup). dashboard/static/app.js: GET-only render engine (esc() on every inject, per-section renderers). Python kept as JSON transport only: _page/_cards deleted (288 lines), static serving + theme substitution, /ask HTML retired (bot is JSON + JS).
- Sections per spec: 11 nav + dual-mode Ask wired / Instruct dormant-disabled + kill readout-with-reason + light/dark toggle + theads/legends/empty states. Email 17, providers, CRM, queue, bot coverage extended in suite.
- 124/124 green (117 + 7 new/extended). Q binding audit green (12/12 renderers, zero dummies, no remnants, 12/12 pages serve, zero rows).

## Deviations
- None from spec. Ask form degrades to raw JSON without JS (acceptable: read-only data, no action).

---

# REBUILD M1 (shell, tokens, Machine, emergency stop, demo layer)

## Implemented (AGENT-HANDS, rebuild doc)
- Branch dashboard-rebuild. system_state table (migration 0002 + Store DDL + get/set_stop audited); stop enforced in run_and_persist + consumer pre-send (SystemStopped); fixtures seeded Stopped.
- Kill backend real: GET /api/status (state/stop/heartbeat/preflight), POST /api/kill-switch (engage always; release gated, 409 on failing checks; suppression line informational).
- Shell replaced globally: 10 items/4 groups, topbar (title+subtitle, demo switch default-ON, status pill, bell, theme toggle, EMERGENCY STOP), stop banner slot. Machine rebuilt blocks A-E (banner, 7-stage pipeline strip, 4 KPI tiles, activity, attention, component health).
- Demo: 10 fixture files (?demo=1 serves same-shape fixtures; kill/status/uploads always real). Fixture counts verified in suite (40/25/30/3/12/7/8/3).
- 130/130 green. Fonts: system stacks first (Inter/JetBrains Mono named, no CDN; local bundling infeasible offline - recorded deviation).

## Deviations
- Suppression pre-flight line informational (no artifact exists); release gates on the 4 verifiable checks. Fonts not bundled (system-stack-first). Old section contents remain until M2/M3 (banned strings there recorded pending).

---

# AGENT SWEEP FIXES (2026-09-28, build-error-resolver + code-reviewer + harness-optimizer + security-reviewer)

## Implemented (against the four agent reports, @coding-standards)
- Store: probe_writable() public probe under lock; central redact_for_audit in transact (post-fn, preserves inbound outcomes aliasing); defensive _export_entity_id (None/non-numeric to 0, audit keeps verbatim str).
- Nodes/inbound/consumer: explicit redact at run_and_persist seam; inbound INBOUND_BATCH_MAX=100 guard; consumer stop-flag read once per pass.
- Live: ZohoSmtpSender super().__init__(); LiveEmailSender inits last_route; smoke scripts print exception type-names only.
- Dashboard: compliance_view scans orchestrator/live.py (dead live.py-unreadable signal restored); _day_count allowlisted; agents_view 2-query; leads drill-down batched (threads/messages/intents); _outbox_stats single-query; machine_status split (_machine_attention/_machine_counts); Handler split (_handle_demo/status/machine_or_issues/static/page/api_view + _send_json/_view_failed with redacted logging); _send security headers (CSP/nosniff/DENY/no-referrer); kill-switch POST Origin-checked + JSON-only (415) + optional OPERATOR_TOKEN (constant-time, 401) with localhost-open default; docstrings corrected.
- Frontend: machine.js bell renders title with message fallback.
- Config: requirements (pytest/sqlalchemy/alembic, langgraph dropped as unused); pytest.ini asyncio scope; .env.example mirrors posture.CHANNELS + live reads (OUTREACH_LIVE, OMNIROUTE/OLLAMA/FREELLM, SMTP sets, AGENTMAIL base/path, OPERATOR_TOKEN); .gitignore *.db/vault.
- Harness (config-only): .opencode/commands/harness-audit.md; AGENTS.md scope pointer; .opencode/agents/ x4.
- Suite: +3 regression pins (compliance live scan, kill 415/403, security headers). 133/133 green.

## Deviations
- dashboard/app.py not file-split (stays one module): handler/views split into helpers instead � full file split deferred to avoid durability-suite churn. Oversize-file HIGH carried as tech debt.
- Kill-switch auth open when OPERATOR_TOKEN unset (localhost bind remains the guard): preserves existing engage/release contract + suite; set OPERATOR_TOKEN to close.
- No TLS change (localhost-only still); bearer + Origin guards land first per minimal-diff rule.
