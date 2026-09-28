# Parity contract B6-PREP — live tier skeleton (AGENT-A, pre-build)

No vendor mirror this batch (novel surface: this project never had an
executable outbox consumer). Vendor grounding where it applies: at-least-once
delivery is the only honest contract over SMTP/REST (no provider here offers
a cross-process exactly-once primitive we can verify dry); AG2's own
`Envelope.idempotency_key` is "reserved for cross-process transports" and
"ignored" in-process (`network/envelope.py:111-113`) — i.e. even upstream
deduplicates at the ACTION layer, not the transport. We do the same.

## 1. Send semantics (picked + defended)

AT-LEAST-ONCE with idempotent effects. Exactly-once is unimplementable over
the transports in scope (no verifiable provider-side dedupe key), so the
contract is: every claimed outbox row is attempted until acknowledged or
dead-lettered; every EFFECT is guarded by `idempotency_key` (actions table,
OR IGNORE) plus the claim lease (no two workers hold one row). Duplicates
can arrive; duplicates cannot doubly act. Dead-letter (not silent drop)
ends every exhausted row, fully audited.

## 2. Failure handling

Per-row attempts counted (`pending_export.attempts`, incremented at claim).
Transient failure → leave claimed: the 600 s lease reclaims it (no new Store
API; `release_export` is a B6-LIVE follow-up only if tighter backoff is
needed). `attempts >= 3` → dead path: `mark_export_done` + `dead_lettered`
audit event carrying the reason. (Correction, AGENT-A+C, 2026-09-27: the
pre-build draft specified a failed `actions` row here, but generic export
rows carry no lead anchor and `actions.lead_id` is FK-bound — writing one
would invent anchor semantics. Deaths live in audit; sends stay guarded at
queue time by Rule 7 keys. No silent path either way.)

## 3. Key-to-send mapping

`sha1(lead_id|draft_hash|channel)` (Rule 7) is BOTH the actions-table guard
AND the provider request token wherever the transport accepts one (AgentMail
metadata / Gmail `Message-Id` domain-scoped suffix — transport detail,
B6-LIVE). Same logical send retried = same key = one effect row. C proves
this with double-queue tests, not prose.

## 4. REDACTION-BEFORE-TRANSACT (CRITICAL)

The audit chain is append-only and hash-chained: anything written into a
payload is PERMANENT. A leaked secret cannot be removed, only superseded.
Rule (binding on all live paths — consumer, live adapters, campaign-live):
every payload passes `redact_for_audit()` before `Store.transact()`. B1–B5
dry paths are untouched (retro-edit avoidance); the rule binds all NEW live
code and any B6-LIVE activation of old paths. Pinned test: a payload
carrying `api_key`/`secret`/`bearer`/private-key-shaped values stores ONLY
the redacted form; a repo scan asserts the raw value appears nowhere (not
in audit, not in logs).
Retro-edit triple-gate (defined here; standing state referenced it):
(1) A documents necessity, (2) Q re-runs every affected batch pin,
(3) CHANGE-NOTES records the edit. No silent retro-edits, ever.

## 5. Kill-switch (no ambiguity)

One flag: `OUTREACH_LIVE=1` enables; anything else (unset/0/other) is
dry_run. Posture is sampled ONCE per run (`posture_snapshot()` into ctx);
in-flight runs COMPLETE under their starting posture; the flip bites on the
next run. Rationale: aborting mid-run risks half-effects, while at-least-
once + idempotency makes completion safe; runs are short-bounded so the
flip is effectively instant. `is_live()` reads env on every call (no
caching — caching would delay the kill).

## 6. Per-credential activation

Channels (independent): `llm/omniroute` (OMNIROUTE_API_KEY),
`llm/freellmapi` (FREELLMAPI_API_KEY), `email/gmail`
(GMAIL_CLIENT_ID/_SECRET/_REFRESH_TOKEN), `email/zoho`
(ZOHO_CLIENT_ID_0[1-4] + SECRET + REFRESH_TOKEN + ACCOUNT_EMAIL per slot —
the approved 4-account fleet; `_05` vars are deprecated and IGNORED, Q
flags any live read of them), `email/agentmail` (AGENTMAIL_API_KEY_A).
"Activated" = env present AND Q per-channel scan clean AND channel smoke
passed. `channel_status()` reports names-and-booleans ONLY — values never
enter logs, payloads, or findings. Adapters read `os.environ` at call time;
no value is ever stored, cached, or committed.

## 7. Checklist P-B6-1..13 (Q verifies; A closes)

- [ ] P-B6-1 Posture defaults dry; flag flip instant for new runs.
- [ ] P-B6-2 Snapshot semantics: in-flight completes under starting posture.
- [ ] P-B6-3 Live adapters paused without creds+flag; zero network code
  reachable in that state (transport seam stubbed, tests fake it).
- [ ] P-B6-4 Consumer: claim → send → done+audited; transient → lease-held;
  exhausted → dead-lettered + audited. All with mocked transport, no network.
- [ ] P-B6-5 Key-to-send mapping pinned by double-queue tests.
- [ ] P-B6-6 REDACTION pinned: secret-write attempt stores redacted-only.
- [ ] P-B6-7 Kill-switch test: flip mid-flight changes nothing in-flight,
  everything after.
- [ ] P-B6-8 Per-channel status reports booleans, never values.
- [ ] P-B6-9 `_05` deprecated: no live read path references it.
- [ ] P-B6-10 Zero B1–B5 modifications (or triple-gated retro-edits).
- [ ] P-B6-11 Static scans + secrets-audit protocol executed and recorded.
- [ ] P-B6-12 First-live-send probe SPEC written (recipient, chain+outbox+
  envelope verification, per-channel unlock order). Executes in B6-LIVE.
- [ ] P-B6-13 Append-only artifacts; 65/65 + new tests green.

## 8. Channel 2/4 — llm/ollama (AGENT-A, 2026-09-27; executed same day)

Same seam, same redaction/metering/audit law as §1–§6. Differences, all
specified: keyless auth handled EXPLICITLY — no Authorization header is
built anywhere on the ollama path (a silent dummy key would be
indistinguishable from a leak; pinned by test). Fail-closed on missing
`OLLAMA_BASE_URL` or `OLLAMA_MODEL`. Missing-var pause reasons name names,
never values (applies to all live adapters). Local inference: usage rows
carry `cost_estimate: 0.0`, `cost_basis: local-inference`.

HONESTY RULE (standing text): ollama proves plumbing, not brain. No finding
may imply draft-quality or persona-behavior verification until a funded
frontier model runs. Smoke proves: transport works, metering lands, audit
closes, chain verifies. Nothing more.

SMOKE PARAMETER CONVENTION: model is passed per smoke invocation
(`OLLAMA_MODEL` in process env); it stays UNSET in committed/shared config
until a production default is chosen. No guessed defaults in code.

## 9. Q probe queue + first-live-send protocol (original B6-PREP text follows)
Mocked-credential send/retry/dead-letter/audit flows; kill mid-flight;
secret-plant redaction; channel-status value-leak scan; `_05` reference
grep. FIRST-LIVE-SEND (B6-LIVE, mandatory): one designated test recipient
per channel → Q verifies chain + outbox + envelope on the REAL send →
per-channel unlock → campaign scale LAST. Campaign-scale before per-channel
verification is a process MAJOR.

## 10. STAGE P1 — outreach transport law (AGENT-A, 2026-09-27)

Two transports, one seam pattern (env-at-call-time, fail-closed, contract
returns, metering, pause-first). No vendor mirror (novel surface); the
inference channels (§8 precedent) are the pattern source.

T1. SMTP/IMAP channel — ONE implementation, credential sets per config:
`SmtpSender` parameterized by env prefix. `email/gmail` reads
`GMAIL_SMTP_{HOST,PORT,USER,PASS}`; `email/zoho` slot `0i` (01–04) reads
`ZOHO_SMTP_0i_{HOST,PORT,USER,PASS}`. Sending via `smtplib` (stdlib);
reading via `imaplib` envelopes (poll only in P1 — full ingestion is later).
OAuth2 (XOAUTH2) is a LIVE-session detail gated on refresh tokens, not a
P1 shape; P1 authenticates the test doubles with planted fakes.
T2. AgentMail API channel — `AgentMailSender._transport` POSTs
`{AGENTMAIL_API_BASE_URL}{AGENTMAIL_SEND_PATH}` with key-at-call-time
bearer. `AGENTMAIL_API_BASE_URL` REQUIRED (no default — the vendor base is
not verified in-repo). `AGENTMAIL_SEND_PATH` defaults to `/messages`,
marked UNVERIFIED_PENDING_LIVE_DOCS: the first-live-send protocol for
agentmail MUST verify the path against live docs before any real send.
T3. Lease window: `OUTREACH_LEASE_SECONDS`, default 600 (matches the
consumer claim lease). Grant/expiry/expiry-with-unknown-outcome are sender
states: a send exceeding the lease WITHOUT a confirmed outcome must
RESOLVE via the dedupe key (`resolve_unknown(store, key)` → actions-table
hit means already-effected → suppress resend) — NEVER blind-resent.
Timeouts are unknown-outcome, not failures.
T4. Credential-shaped (L1 audit vocabulary, names only, never values):
gmail 4 names, zoho 16 names (4 slots × 4, `_05` excluded), agentmail 2
names (`AGENTMAIL_API_BASE_URL`, `AGENTMAIL_API_KEY_A`). No dummy secrets
in fixtures — fakes are planted via monkeypatched env with `fake-` values
(B6-PREP pattern) or in-process fake servers holding no credentials at all.

Checklist ref: P1 items live in the batch order (fail-closed ×3, lease
dedupe, honest retry, dead-letter, zero B1–B5 touches). Q pins each.

## 11. Inbound reply loop law (AGENT-A, 2026-09-27 — R5, dry)

New attack surface, hostile by default (decision 9: block+quarantine stands).
Pipeline with NO send-side changes — `reconcile()` READS actions, never
sends: poll envelopes (injected fetcher; IMAP/AgentMail adapters land
per-channel with creds) → DEDUPE in a distinct keyspace
(`inb:{provider}:{message_id}` via `messages.provider_message_id` UNIQUE
partial index — never the send keyspace) → LINKAGE to threads by exact
In-Reply-To/sent-id match ONLY (unknown reference → NEW thread, never
fuzzy-attached; spoofed keys land isolated by construction) → INSERT inbound
message QUARANTINED BY DEFAULT (body is untrusted data; human/policy clears)
→ AUDIT entry per message → TERMINAL pre-filter: deterministic
unsubscribe/bounce signals (regex gate, Rule 3 — gates may be regex,
deciders may not) set lead stage + terminal flags through the compliance
path. LLM classification stays paused (dry); no auto-reply, no auto-send,
no draft mutation from inbound content — any path from an inbound row to an
outbound effect is a CRITICAL finding. Fetcher failures fail closed (zero
rows). Malformed envelopes (missing ids, hostile headers) are quarantined,
never dropped silently, never trusted.

## 12. Activation-rehearsal scope law (AGENT-A, 2026-09-27 — dry only)
Rehearsals walk the runbook sessions against in-process fakes with ZERO
network and ZERO real creds: gmail/zoho/agentmail activation (env checks →
smoke-env checks → smoke invocation shape → first-live-send steps →
inbound poll slot → pause/recovery), plus omniroute un-dormancy
(funding → posture flip → MANDATORY rotation → smoke). Rehearsal rules:
flag runbook gaps instead of improvising past them; rotation precedes first
smoke (never after — a chat-exposed key must not send even once); abort
branches must name what pauses, what stays open, and the exact recovery.
No-new-files discipline (operator standing order): all rehearsal evidence
lands as notes in the cycle report; existing docs take appends only.

## 13. Operator dashboard law (AGENT-A, STAGE D — read-only prime directive)

PRIME DIRECTIVE: the dashboard is READ-ONLY over all state. It must never
mutate state, trigger sends, flip postures, or write to the chain. Every
query path is provably non-mutating (C demonstrates, Q attacks). A dashboard
that can act is a second kill-switch surface — it gets none.
Data source: ledger, gates, posture, channel states, spend rows, terminal
states — everything shown traces to append-only artifacts or live-computed
read-only scans. No dashboard-private state; underivable values are not shown.
Views: machine (5 channels + posture + last smoke per channel, all derived),
ledger (recent chain entries, spend from usage payloads, terminal stages,
dead-lettered/unknown needing reconcile), compliance (placeholder-shape scan
computed live on demand, posture, OUTREACH_LIVE, kill-switch state, L1),
operator queue (open-item template from the runbook with per-item live status;
test-recipient shows UNDESIGNATED until a designation store exists — absence
rendered honestly, never filled in).
Runtime law: binds 127.0.0.1 by default; non-localhost bind requires
ALLOW_REMOTE=1 (explicit opt-in). Render path inherits redaction: every
value passes redact_for_audit before render; error handlers reflect nothing
 caller-supplied and no env values. Only GET is served (all other methods
405); unknown paths 404 without reflection.
