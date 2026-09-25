# Genspark Superagent — BUILD PART 2 of 2
### Scope: reply classification, the supervisor chat console, final Paperclip/watchdog wiring, and final packaging. This assumes Part 1 (ingestion, copywriter, sender, compliance-gate wiring, end-to-end dry-run) is complete. Do not redo Part 1's work — audit it first, per Task 0 below.

## What you are, and the one rule that matters most
You are continuing an existing, partially-built codebase. Part 1 of this build finished the core ingest → copywriter → verify → compliance-gate → send pipeline in dry-run. Your job is everything that makes the system operate continuously: classifying replies, giving the human a way to ask the swarm questions, finishing the monitoring wiring, and packaging the whole thing for handoff to real execution on the user's laptop.

**You have no real credentials of any kind**, same as Part 1. No Zoho, Gmail, AgentMail, or Obsidian REST credentials exist in this session, and you must not ask for them. Every test in this session uses fabricated mock data and mock/local storage. Never claim you called a live external service — you did not and cannot in this session.

**Do not re-research the 16 source repositories.** That work is already done and cited in the existing compatibility report. Only open a source repo directly for a detail genuinely not already covered.

## The resume protocol — read this before doing anything else

**Check first:** has a file named `PROJECT_STATE.md` been provided, and does it say `Part: 2 of 2`?

- **If `PROJECT_STATE.md` says `Part: 1 of 2 — PART 1 COMPLETE`** → this is the start of Part 2. Do Task 0 (audit Part 1), then begin the Part 2 task list at Task 1.
- **If `PROJECT_STATE.md` says `Part: 2 of 2` and lists some tasks DONE** → this is a RESUME of a Part 2 session that ran out of credit. Re-run the recorded self-test for every file marked DONE before trusting it; treat any failure or missing file as NOT STARTED and rebuild it. Resume at the first task that is NOT STARTED or IN PROGRESS.
- **If no `PROJECT_STATE.md` exists at all** → stop. Part 1 has not been completed or its export was not provided. Report this and do not proceed with fabricated assumptions about what Part 1 contains.

**The rule for every task, in both cases:** finish a file completely — including its self-test passing — before moving to the next one. Never leave a file half-written across a session boundary.

**After completing each task**, immediately update `PROJECT_STATE.md` with that file's path, the exact self-test command you ran, and its exact result, before starting the next task.

## Part 2 task list, in strict order

**Task 0 [Mandatory audit of Part 1 — do not skip].**
Re-run every self-test recorded in `PROJECT_STATE.md` from Part 1: the ingest, copywriter, sender, and compliance-gate-wiring self-tests, and the end-to-end dry-run integration test. Report each result. If any regression is found, fix it before writing any new code for Part 2 — Part 2's work depends on Part 1's pipeline being correct, and building reply classification on top of a broken ingestion/compliance chain would compound the mistake rather than isolate it.

**Task 1 [Write `orchestrator/nodes/reply_classifier.py`].**
- Port the conversation-stage logic from SalesGPT's `determine_conversation_stage()` (already located precisely in the compatibility report at `salesgpt/agents.py:134`, with the stage dictionary at `:66`) — copy this specific logic, do not adopt the surrounding SalesGPT runtime.
- Extend it to output a six-way intent classification: `interested`, `not_interested`, `objection`, `auto_reply`, `bounce`, `unsubscribe`.
- On `unsubscribe` or `bounce`, the node must call the existing `reporter.py`'s write path to set the relevant `TERMINAL_FRONTMATTER` field — reuse that existing allowlist-enforcing function; do not write frontmatter directly and do not bypass the existing write-guard.
- **Self-test:** six mock reply texts, one per intent, all correctly classified, plus one deliberately ambiguous reply confirming the classifier does not force a confident label it can't support. Separately confirm that an `unsubscribe` classification actually triggers the terminal-field write and that a subsequent mock send attempt for that lead is blocked by the existing compliance gate.

**Task 2 [Build the supervisor chat console].**
This was designed in the compatibility report but not actually written in Part 1 or before — confirm this by checking the existing file list, and if it's genuinely absent, write it now as `supervisor/console.py`.
- Mount exactly five read-only tools: `get_lead_status(lead_id)`, `get_daily_metrics(date)`, `get_sender_health()`, `get_agent_run(agent_id)`, `search_related_patterns(topic)`. All read from Obsidian and/or Paperclip only.
- **No write or send tool may be mounted, at all.** This must be enforced structurally, not just documented: write a self-test that attempts to invoke a write/send action through the console's own tool-calling interface and confirms no such tool exists to call — not that it exists but refuses, that it is simply absent.
- Every answer the console gives must cite the exact note path and section it came from — quote the underlying record, don't paraphrase it into something that could drift from what actually happened.
- **Self-test:** populate a handful of mock Obsidian notes and mock Paperclip activity records, ask the console three questions drawn from the compatibility report's example list (e.g., "why was this lead blocked," "how many replies today"), and confirm each answer correctly cites its source note/section.

**Task 3 [Finish Paperclip adapter and watchdog wiring].**
- Confirm `paperclip-adapter/heartbeat.py` and `watchdog/watchdog.py` (already written) are actually called from the orchestrator's main run loop, not sitting as disconnected standalone scripts. Wire them in if they are not already connected.
- **Self-test:** simulate one failed agent run in a mock and confirm exactly one incident note is produced and exactly one watchdog notification fires — reuse the existing dedupe self-test logic rather than rewriting it.

**Task 4 [Full-system dry-run smoke test].**
- Simulate a small batch — three fabricated leads — through the complete pipeline end to end: ingest → copywriter → verify → compliance gate → sender (dry-run) → a simulated reply → reply classifier → reporter → the supervisor console answering one question about this batch.
- All external network calls remain mocked. All other code must genuinely execute, not be described.
- Show the full trace for all three leads, including at least one that ends in a simulated unsubscribe and is confirmed blocked from further contact.

**Task 5 [Final self-check against the full boundary list].**
Go through each of these and confirm the actual code — not the design — satisfies it. Do not summarize this as "yes to all"; check and report each individually:
- No separate vector/SQL database or framework-native long-term memory store holds lead data anywhere in the code.
- Every durable fact is mirrored to Obsidian in the same turn it's produced.
- Terminal compliance states (`unsubscribed`, `bounced`, `do_not_contact`, `jurisdiction_hold`) are enforced by plain deterministic checks, never by an LLM's judgment.
- The compliance gate is checked immediately before every simulated send, not only at planning time.
- No skill-loading path in the code references ClawHub.
- The supervisor console has no write or send tool mounted.
- Every agent action that produces a claim passes through the existing verifier before being accepted.

**Task 6 [Final packaging for laptop handoff].**
- Update `PROJECT_STATE.md` to read: `Part: 2 of 2 — STATUS: PART 2 COMPLETE — ready for laptop execution`.
- List every file touched in this session, its self-test command, and its exact result.
- Write a separate `HANDOFF-TO-LAPTOP.md` stating plainly what could **not** be done in this sandboxed session and must happen on the real laptop instead: obtaining and entering real Zoho/Gmail/AgentMail/Obsidian credentials, running the Phase-0 live-service validation gates, connecting to a real Obsidian vault, and turning off dry-run mode. Be explicit that nothing in this export has been tested against a real external service.
- Produce the final export archive containing the complete project.

## If you cannot finish Task 6 before credit runs out
Stop at the most recently fully-completed task. Ensure `PROJECT_STATE.md` accurately reflects what's DONE and what's next, export the archive as-is, and stop. The next session will be given this same prompt again along with `PROJECT_STATE.md` and the exported archive, and will resume correctly.
