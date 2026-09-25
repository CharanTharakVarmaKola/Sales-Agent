# PROJECT_STATE.md

Part: **1 of 2 — STATUS: PART 1 COMPLETE — ready for Part 2**

Last updated: 2026-09-24 (Part 1 session, Genspark Superagent)
Project root in export: `outreach-swarm/`
Credential rule honoured: **no live service was called at any point.** Every
external dependency (OmniRoute, FreeLLMAPI, Zoho, Gmail, AgentMail) is behind a
swappable interface whose live implementation refuses to run without
credentials; every test ran on mocks and a local temp folder standing in for the
vault.

## Resume instructions for the next session

1. Read this file literally.
2. For every file marked DONE, re-run the exact command in the "self-test" column.
   If it still passes, treat it as done. If it fails or the file is missing,
   treat it as NOT STARTED and rebuild it.
3. Resume at the first task marked NOT STARTED or IN PROGRESS, in task order.
4. Update this file after each task.

## Task status

| Task | Scope | Status | Self-test command (recorded) | Result (recorded) |
|---|---|---|---|---|
| 0 | Audit the existing scaffold before writing anything new | DONE | `python3 orchestrator/policy/compliance_gate.py` | 9 passed, 0 failed |
| 0 | (audit) | DONE | `python3 scripts/phase0/validate_compliance_gate.py` | 9 passed, 0 failed |
| 0 | (audit) | DONE | `python3 scripts/phase0/validate_verification_loop.py` | 6 passed, 0 failed |
| 0 | (audit) | DONE | `python3 mcp-servers/email-outreach/server.py --selftest` | 18 passed, 0 failed |
| 0 | (audit) | DONE | `python3 scripts/phase0/check_clawhub_refs.py` | 19 passed, 0 failed |
| 0 | (audit) | DONE | `python3 -m compileall -q .` | exit 0, no errors |
| 0.5 | `orchestrator/obsidian_client.py` REST → direct file I/O | DONE | `python3 orchestrator/obsidian_client.py --selftest` | 19 passed, 0 failed (incl. 2-process + 2-thread concurrent `patch_section`) |
| 1 | `orchestrator/nodes/ingest.py` | DONE | `python3 orchestrator/nodes/ingest.py --selftest` | 20 passed, 0 failed |
| 2 | `orchestrator/nodes/copywriter.py` | DONE | `python3 orchestrator/nodes/copywriter.py --selftest` | 15 passed, 0 failed |
| 3 | `orchestrator/nodes/sender.py` | DONE | `python3 orchestrator/nodes/sender.py --selftest` | 15 passed, 0 failed |
| 4 | `orchestrator/graph/build.py` (wire the compliance gate) | DONE | `python3 orchestrator/graph/build.py --selftest` | 15 passed, 0 failed |
| 5 | End-to-end integration test | DONE | `python3 scripts/phase0/e2e_integration.py` | 14 passed, 0 failed |
| 6 | Final packaging for this part | DONE | archive built + uploaded | see BUILD_PART1_REPORT.md |

Supporting modules (written in this session, each with its own self-test):

| File | Self-test | Result |
|---|---|---|
| `orchestrator/policy/reporter.py` | `python3 orchestrator/policy/reporter.py --selftest` | 9 passed, 0 failed |
| `orchestrator/nodes/verifier.py` | `python3 orchestrator/nodes/verifier.py --selftest` | 9 passed, 0 failed |
| `compliance_gate.py` (root shim) | `python3 compliance_gate.py` | 9 passed, 0 failed |

`orchestrator/graph/engine.py`, `orchestrator/inference/*`, `orchestrator/openclaw/*`,
`vault-schema/Templates/Lead.md` are libraries/assets exercised by the row-1..5 tests above.

## Full-suite result (last run, 2026-09-24)

```
compileall exit=0
orchestrator/policy/compliance_gate.py            exit=0   9 passed, 0 failed
orchestrator/policy/reporter.py --selftest        exit=0   9 passed, 0 failed
orchestrator/obsidian_client.py --selftest        exit=0  19 passed, 0 failed
scripts/phase0/validate_compliance_gate.py        exit=0   9 passed, 0 failed
scripts/phase0/validate_verification_loop.py      exit=0   6 passed, 0 failed
scripts/phase0/check_clawhub_refs.py              exit=0  19 passed, 0 failed
mcp-servers/email-outreach/server.py --selftest   exit=0  18 passed, 0 failed
orchestrator/nodes/ingest.py --selftest           exit=0  20 passed, 0 failed
orchestrator/nodes/copywriter.py --selftest       exit=0  15 passed, 0 failed
orchestrator/nodes/sender.py --selftest           exit=0  15 passed, 0 failed
orchestrator/nodes/verifier.py --selftest         exit=0   9 passed, 0 failed
orchestrator/graph/build.py --selftest            exit=0  15 passed, 0 failed
scripts/phase0/e2e_integration.py                 exit=0  14 passed, 0 failed
compliance_gate.py                                exit=0   9 passed, 0 failed
```

`external_http_calls` was empty in every run; no live route was exercised.

## Deferred to Part 2 (nothing deferred is required by Part 1)

1. **Replies** — inbound reply ingestion, classification (interested / not-now /
   unsubscribe / bounce) and writing the classification back into the lead note.
   Part 1 stops at a dry-run send plus the run-log line.
2. **Supervisor console** — the human approval/inspection surface over the vault.
3. **Monitoring** — long-running health/metrics view; Part 1 only records
   `external_http_calls`, `attempted_provider_calls` and incident notes.
4. **Final packaging** — the Part-2 archive, once replies/console/monitoring exist.
5. **Live credentials and real transports** — putting actual keys into
   `OMNIROUTE_API_KEY` / `FREELLMAPI_API_KEY` / `ZOHO_API_KEY` / `GMAIL_API_KEY` /
   `AGENTMAIL_API_KEY`, and turning on the real provider adapters. Deliberately not
   attempted: this session has no credentials of any kind, by design.
6. **Bounce/complaint suppression refresh** — Part 1's suppression list is a static
   local constant; a live refresh path belongs with the reply pipeline in Part 2.

## Known limitations (Part 1 boundary, stated honestly)

* `send_outreach(dry_run=False)` is written but *never* succeeds here: with no
  credentials it returns `status="provider_unavailable"` and records the attempt in
  `attempted_provider_calls`. That is the intended, tested behaviour, not a bug.
* The graph executes on the local engine in `orchestrator/graph/engine.py`. The
  `langgraph` package is detected and its `StateGraph` is imported when present,
  but the recorded test traces come from the local engine so results are stable
  across package versions.
* `max_leads=1` per graph run — batching a whole CSV through one run belongs to Part 2.
