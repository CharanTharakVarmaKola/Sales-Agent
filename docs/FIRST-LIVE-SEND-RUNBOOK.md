# FIRST-LIVE-SEND RUNBOOK (DRAFT — AGENT-Q, pre-creds; executes in B6-LIVE)

Status: UNEXECUTED. No step here runs until B6-PREP gate + creds + staged order.

## Staging (mandatory, pre-live)
- [ ] S1 Secrets rehearsal: plant a fake secret through every live-path
  payload builder; assert stored form is redacted-only (B6-PREP pin holds).
- [ ] S2 Kill-switch rehearsal: set flag, snapshot, unset flag, assert
  in-flight completes + new runs pause (B6-PREP pin holds).
- [ ] S3 Channel inventory: `channel_status()` booleans recorded per channel;
  no values leave the machine. Missing env = channel stays dark, no override.

## Activation day (per channel, in this order — never globally)
- [ ] L1 Static scans + secrets audit for the channel (Q protocol, recorded).
- [ ] L2 Smoke: ONE send to the designated test recipient on that channel.
- [ ] L3 Q verifies on the REAL send: audit chain + outbox row (done) +
  envelope + idempotency (re-queue same key → one effect row).
- [ ] L4 Per-channel live unlock recorded as an audit event.
- [ ] L5 Campaign scale LAST, and only after all active channels pass L1–L4.

## Abort conditions (any one kills the day, kill-switch first)
- Unexpected paused→live transition (flag/flip anomaly).
- Any raw secret-shaped value in audit, logs, or findings.
- Duplicate effect row under one idempotency key (B6 blocked until closed).
- Chain verification failure at any step.

## Standing rules restated
Env/secret-store only — never chat, never git, never fixtures.
Rotation-on-accident, never scrubbing (chain is append-only).

## Channel notes (AGENT-Q)
- llm/omniroute: status DORMANT-AWAITING-FUNDING (402, $0.00 spent; L2→L4
  re-run on funding + model confirm). Keyed channel — full secrets-audit.
  ROTATION FIRST: the §12 law (rotation precedes first smoke) supersedes
  the earlier post-smoke trigger note — the key is chat-exposed and must
  not send even once unrotated. Corrected 2026-09-27, no violation occurred
  (smoke never ran).
- llm/ollama: KEYLESS channel — audit is simpler by construction (no key
  exists: nothing to leak, nothing to rotate; rotation trigger N/A).
  Verified: no Authorization header is ever built on the ollama path
  (pinned test). L1 for ollama = reachability + shape + metering checks.
- Lease-vs-latency: deferred to the first outreach channel (SMTP-specific).

## B6-LIVE omniroute notes (AGENT-Q, 2026-09-27)
- Lease-vs-latency check DEFERRED to the first outreach channel (SMTP-specific).
- S2 inference adaptation CLOSED (stubbed, zero spend): crash→zero rows,
  retry→honest second attempt row, chain verifies.
- Channel status 2026-09-27: `GET /models` 200 (key valid); smoke BLOCKED on
  wallet 402, $0.00 spent. Re-run L2→L4 on funding + model confirm.

## STAGE P1 rehearsals — EXECUTED vs fakes (AGENT-Q, 2026-09-27)
- Lease-vs-latency: slow fake (socket.timeout post-DATA, lease 1 s) →
  SendUnknown with key → late effect recorded → reconcile suppresses resend
  (1 wire attempt, chain verifies). Resolution path proven, no blind resend.
- Crash-after-send (fake transport): covered by rehearsal + suite.
- L1 placeholder shapes: gmail 4 / zoho 16 / agentmail 2 env NAMES audited
  present-in-code, values absent everywhere (suite pins; `_05` excluded).

## Per-channel L-checklists (fill-in-the-blank for live sessions)
- Gmail: [ ] app-password/OAuth refresh in env [ ] L1 name audit [ ] test
  recipient send [ ] chain+outbox+envelope verify [ ] unlock event.
- Zoho 01–04: same, per slot; [ ] confirm single-app shared client ID.
- AgentMail: [ ] VERIFY send path against live docs (UNVERIFIED default)
  [ ] key in env [ ] test send [ ] chain verify [ ] unlock event.
- Inbound (per channel, pre-staged): [ ] poller creds in env [ ] fetcher
  fail-closed rehearsal vs fakes [ ] spoofed-key isolation rehearsal
  [ ] terminal-gate calibration on real samples [ ] quarantine-clear
  procedure named [ ] first live poll observed (read-only) before any
  policy-clear automation.

## Env var manifests (AGENT-C, rehearsed 2026-09-27 — exact key names, zero
ambiguity at cred delivery; values never appear here)
- Global gate: OUTREACH_LIVE=1 (unset/0 = dry everywhere).
- llm/omniroute: OMNIROUTE_API_KEY, OMNIROUTE_BASE_URL (default
  https://api.cheaperinference.com/v1), OMNIROUTE_MODEL (per-invocation,
  no default).
- llm/ollama: OLLAMA_BASE_URL, OLLAMA_MODEL (per-invocation).
- llm/freellmapi: FREELLMAPI_API_KEY. (dark — no local server confirmed.)
- email/gmail-smtp: GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, GMAIL_SMTP_USER,
  GMAIL_SMTP_PASS. (OAuth client trio stays the live-session XOAUTH2 path.)
- email/zoho-smtp slots 01–04: ZOHO_SMTP_0{i}_HOST/PORT/USER/PASS.
  (`_05` names do not exist in any live read path — rejected if sent.)
- email/agentmail-api: AGENTMAIL_API_BASE_URL, AGENTMAIL_API_KEY_A
  (+ optional AGENTMAIL_SEND_PATH; default /messages UNVERIFIED).
- Lease: OUTREACH_LEASE_SECONDS (default 600, all consumers).
- Rehearsed 2026-09-27: all four sessions walk green vs fakes (14/14);
  slot isolation holds; pause re-darkens; rotation pickup needs no call.

## Abort clauses (AGENT-Q — what pauses, what stays open, recovery; no
judgment calls during live sessions)
- Auth failure mid-session (any channel): channel stays dark (paused=True);
  zero sends (fail-closed proven); open = the session itself; recovery = fix
  creds, re-run that channel's session from L1. Other channels unaffected.
- Unknown-outcome mid-send: do NOT resend — reconcile by dedupe key; if the
  key is effected, suppressed; if absent, exactly one honest retry, then
  lease/dead-letter rails. Recovery is the rails, not a decision.
- Hostile inbound flood: quarantine holds by default; stages move only on
  deterministic gates; open = human clear queue; recovery = named clear
  procedure (must exist before the inbound live slot — gap if unnamed).
- Chain verification failure at any step: full stop, kill-switch first,
  all channels dark until the break is explained. No partial unlocks.
- OPERATOR GAP (blocks L2 everywhere): no designated test recipient
  address is recorded anywhere. Name one per channel before any live
  session — L2 cannot execute without it.
