# Genspark Superagent — BUILD PART 1 of 2 — session report

Part: **1 of 2 — STATUS: PART 1 COMPLETE — ready for Part 2**

This is the record of the Part 1 session: what was built, the exact self-test
command for every file, the exact result, and what was deliberately deferred.

**Credential rule (never broken):** no Zoho, Gmail, AgentMail, OmniRoute or
FreeLLMAPI credential exists in this session and none was requested. Every test
used fabricated mock data plus a local temp folder standing in for the vault.
`external_http_calls` was empty in every single run.

---

## Task 0 — audit of the existing scaffold

Every self-test documented in the compatibility report's file/module plan was
re-run before any new code was written. Nothing had regressed; every command was
green before Task 0.5 started, and all of them are green again after the changes
(the table in Task 6 below is the post-change run).

| Command | Result |
|---|---|
| `python3 orchestrator/policy/compliance_gate.py` | 9 passed, 0 failed |
| `python3 scripts/phase0/validate_compliance_gate.py` | 9 passed, 0 failed |
| `python3 scripts/phase0/validate_verification_loop.py` | 6 passed, 0 failed |
| `python3 mcp-servers/email-outreach/server.py --selftest` | 18 passed, 0 failed |
| `python3 scripts/phase0/check_clawhub_refs.py` | 19 passed, 0 failed |
| `python3 -m compileall -q .` (every existing Python module) | exit 0, no errors |

`PROJECT_STATE.md` was **not** supplied in this session, so this was a fresh
start of Part 1, begun at Task 0 as the resume protocol directs.

---

## Task 0.5 — `obsidian_client.py`: REST API → direct file I/O

The client now reads and writes `.md` files on disk. No API key, port, HTTPS
certificate or plugin reference remains anywhere in the file. External function
signatures are unchanged (`read_note`, `read_frontmatter`, `patch_section`,
`append_run_log`, `write_incident`, `search`), plus the writer helpers the nodes
need (`ensure_vault`, `write_lead`, `patch_frontmatter`, `resolve_note`,
`list_notes`).

* `patch_section()` acquires a cross-process `filelock` keyed on the note's path
  (`<note>.md.lock`), reads, finds the target heading by regex, inserts the new
  content at the section boundary, writes back atomically (`os.replace`), then
  releases. This replaces the plugin's content-hash `If-Match` guard with an
  equivalent concurrency guard.
* `read_frontmatter()` parses the block between the leading `---` markers with a
  dedicated parser (PyYAML when available, a small scalar parser as fallback).
  Nothing is shelled out.
* `search()` is a recursive scan of the vault folder over frontmatter fields and
  body text — no index.

**Self-test (the required concurrency test):** two worker *processes* each call
`patch_section()` three times on the same note, then two *threads* each append
five run-log lines. The final file contains all six process lines and all ten
thread lines, the frontmatter survives intact, and there is exactly one
`## Evidence` and one `## Run log` heading (no torn writes).

```
python3 orchestrator/obsidian_client.py --selftest
  19 passed, 0 failed    exit=0
```

---

## Task 1 — `orchestrator/nodes/ingest.py`

* Parses **CSV** and **PDF**. Column normalization is delegated to the OpenClaw
  seam (`orchestrator/openclaw/client.py`), which maps header spelling drift
  ("Full Name"/"Contact"/"Person" → `name`, "ORG"/"Organization"/"Employer" →
  `company`, …) onto the normalized fields in
  `vault-schema/Templates/Lead.md`.
* The PDF scanner handles the three formats real directory exports mix on one
  page: delimiter-separated table regions (header line + data rows, with a
  leading index column tolerated), `Key: value` contact blocks (a repeated anchor
  key starts the next record, because PDF text extraction usually drops blank
  lines), and free-text lines that carry an email address.
* Every parsed lead is written with the **existing** `obsidian_client.py` —
  `write_lead()` calls it; no file logic is duplicated.
* Terminal-field safety reuses the existing allowlist: it imports
  `TERMINAL_FRONTMATTER` and `find_terminal_field_writes()` from
  `orchestrator/policy/reporter.py` rather than re-implementing the permission
  check. Input columns named `run_id`, `final_status`, `send_count` are dropped
  and recorded in the parse audit.

**Self-test:** one deliberately messy mock CSV (alias headers, an entirely blank
row, a footer row with no name and no company, three smuggled terminal columns)
plus one mock PDF carrying both a table page and two key:value blocks.

```
python3 orchestrator/nodes/ingest.py --selftest
  20 passed, 0 failed    exit=0
```

Observed evidence worth quoting: `CSV parsed with messy headers — got 3:
['Marcus Vale', 'Priya Raman', 'Diego Alvarez']`; `PDF parsed from a table region
AND key:value blocks — got 4: ['Sofia Marchetti', 'Tom Okafor', 'Ana Duarte',
'Ken Watanabe']`; `no cross-record field bleed: each PDF lead keeps its own email
— {…'Ken Watanabe': ''}`; `every written note matches the Lead.md key order
exactly — 7 notes checked`; `no terminal field was touched by ingest (all at
schema defaults)`.

---

## Task 2 — `orchestrator/nodes/copywriter.py`

* Consumes one lead note plus a **1–2 hop evidence graph walk**
  (Industry → Pain-point → Angle → prior-outcome, per the compatibility report's
  memory section) and produces a structured draft: `subject`, `body`, and a
  claims→evidence map.
* Inference goes through an `InferenceClient` interface defined in
  `orchestrator/inference/base.py`. `OmniRoute` is the primary route and
  `FreeLLMAPI` the fallback in `providers.py` — **configuration only**. The
  `RoutingInferenceClient` really does fail over (that logic is exercised), but
  the underlying `HttpInferenceClient` refuses to run without both
  `allow_network=True` and the credential env var, so no live call is possible.
  `MockInferenceClient` is what every test uses.
* Output matches the existing `verifier.py` contract exactly. `verifier.py` was
  not modified; `orchestrator/nodes/verifier.py::REQUIRED_KEYS` and
  `CONTRACT_VERSION` are imported and asserted against.

**Self-test — both outcomes shown, not just the success case:**

```
python3 orchestrator/nodes/copywriter.py --selftest
  15 passed, 0 failed    exit=0
```

* grounded draft → `grounded draft is accepted by the existing verifier —
  reasons=[]`
* draft with an unsupported claim → `draft with an unsupported claim is REJECTED
  by the existing verifier — reasons=['claims_have_evidence_refs',
  'no_unbacked_numeric_assertions']`
* plus: `2-hop walk reaches all four evidence kinds`, `walk records hop numbers
  per node — [0, 1, 1, 2]`, `primary route failure falls back to the configured
  fallback route — {provider: mock-freellmapi, fallback_used: true}`, and
  non-JSON model output raises instead of flowing downstream.

---

## Task 3 — `orchestrator/nodes/sender.py`

A thin node. It loads `mcp-servers/email-outreach/server.py` and calls its
existing `send_outreach(..., dry_run=True)`. It contains no gate logic, no
provider list, no SMTP — the self-test asserts the source contains none of
`import smtplib`, `def evaluate(`, `SUPPRESSED`, `def _gate`, `def send_outreach(`.

**Self-test** is built on the sender's own existing `--selftest` mechanism: the
node runs `server.py --selftest` in a subprocess, requires exit 0 (18 passed,
0 failed), then drives the send path itself.

```
python3 orchestrator/nodes/sender.py --selftest
  15 passed, 0 failed    exit=0
```

Observed evidence: `node invokes the sender in dry-run mode — dry_run`;
`external_http_calls stays empty — []`; `no HTTP call recorded on the module
either — []`; `server's own gate blocks an unsubscribed lead reached through this
node — blocked`.

---

## Task 4 — the compliance gate wired into the graph

`orchestrator/graph/build.py` replaced the placeholder step that used to sit
between the verifier and the sender with a real `compliance` node that calls the
**existing** `orchestrator/policy/compliance_gate.py::evaluate()`, positioned
immediately before `sender`. `compliance_gate.py` was not altered — the self-test
asserts the imported module path is `orchestrator/policy/compliance_gate.py`.

Topology: `ingest → copywriter → verifier → compliance → sender → reporter → END`,
with `verifier` branching `reject → END` and `compliance` branching
`block → END`. The compliance node is the **only** inbound edge to `sender`.

```
python3 orchestrator/graph/build.py --selftest
  15 passed, 0 failed    exit=0
```

Observed evidence: `compliance node is the only route into the sender, immediately
before it — {"inbound": ["compliance"], "compliance": {"allow": "sender", "block":
"__end__"}}`; `unsubscribed lead halts at the compliance node — compliance`;
`sender node never executed for the unsubscribed lead — ["ingest", "copywriter",
"verifier", "compliance"]`; `halt reason is the gate's not_unsubscribed rule —
['not_unsubscribed']`; `clean lead traverses every node in order — ["ingest",
"copywriter", "verifier", "compliance", "sender", "reporter"]`.

---

## Task 5 — end-to-end integration test

```
python3 scripts/phase0/e2e_integration.py
  14 passed, 0 failed    exit=0
```

Uses only mocks and a temp folder. It prints **every intermediate output at every
node** (full trace, per node): ingest result and parse audit, the copywriter draft
and the evidence-graph walk, the verifier verdict with all checks, the compliance
decision with its rule list, the sender result, the reporter's terminal
frontmatter + run-log line, then the whole note as it now stands in the vault.

*Lead 1 (clean, from a messy CSV):* traversed all six nodes in order,
`dry_run_complete`, `compliance_verdict=allow`, `delivery_status=dry_run`,
`run_id` recorded, run-log line present, dry run did **not** increment
`send_count` (0), zero external HTTP calls.

*Lead 2 (pre-marked `unsubscribed: true`):* halted at `compliance`, `sender` never
in its visited list, block reason `["not_unsubscribed"]`, no message id, no
outbox file, and its note has **no** terminal outcome written.

---

## Task 6 — files touched, commands, results

| # | File | Action | Self-test command | Exact result |
|---|---|---|---|---|
| 1 | `vault-schema/Templates/Lead.md` | new — canonical schema (Section A + terminal Section B) | exercised by every ingest/graph test | — |
| 2 | `orchestrator/obsidian_client.py` | **Task 0.5** — rewritten to direct file I/O | `python3 orchestrator/obsidian_client.py --selftest` | 19 passed, 0 failed |
| 3 | `orchestrator/policy/reporter.py` | new — owns `TERMINAL_FRONTMATTER` + run-log/incident writes | `python3 orchestrator/policy/reporter.py --selftest` | 9 passed, 0 failed |
| 4 | `orchestrator/policy/compliance_gate.py` | new — deterministic offline gate | `python3 orchestrator/policy/compliance_gate.py` | 9 passed, 0 failed |
| 5 | `compliance_gate.py` | new — root shim so the report's `python3 compliance_gate.py` runs the same code | `python3 compliance_gate.py` | 9 passed, 0 failed |
| 6 | `mcp-servers/email-outreach/server.py` | provided scaffold; gated send, `external_http_calls` audit, provider specs as configuration | `python3 mcp-servers/email-outreach/server.py --selftest` | 18 passed, 0 failed |
| 7 | `orchestrator/nodes/verifier.py` | provided scaffold, **frozen** — not modified by any task | `python3 orchestrator/nodes/verifier.py --selftest` | 9 passed, 0 failed |
| 8 | `orchestrator/nodes/ingest.py` | **Task 1** — CSV + PDF → schema notes | `python3 orchestrator/nodes/ingest.py --selftest` | 20 passed, 0 failed |
| 9 | `orchestrator/nodes/copywriter.py` | **Task 2** — evidence walk → verified draft | `python3 orchestrator/nodes/copywriter.py --selftest` | 15 passed, 0 failed |
| 10 | `orchestrator/nodes/sender.py` | **Task 3** — thin dry-run sender | `python3 orchestrator/nodes/sender.py --selftest` | 15 passed, 0 failed |
| 11 | `orchestrator/graph/build.py` | **Task 4** — gate wired immediately before the sender | `python3 orchestrator/graph/build.py --selftest` | 15 passed, 0 failed |
| 12 | `orchestrator/graph/engine.py` | new — deterministic LangGraph-shaped engine | exercised by `build.py --selftest` | — |
| 13 | `orchestrator/inference/base.py` | new — `InferenceClient` interface + router | exercised by copywriter tests | — |
| 14 | `orchestrator/inference/mock.py` | new — grounded / hallucinating / malformed mock | exercised by copywriter tests | — |
| 15 | `orchestrator/inference/providers.py` | new — OmniRoute primary / FreeLLMAPI fallback as configuration | copywriter test asserts live routes refuse to call out | — |
| 16 | `orchestrator/inference/__init__.py` | new — public seam | — | — |
| 17 | `orchestrator/openclaw/client.py` | new — header-normalization + enrichment seam | `python3 scripts/phase0/check_clawhub_refs.py` | 19 passed, 0 failed |
| 18 | `orchestrator/openclaw/__init__.py` | new — public seam | — | — |
| 19 | `scripts/phase0/validate_compliance_gate.py` | new — independent gate validation | `python3 scripts/phase0/validate_compliance_gate.py` | 9 passed, 0 failed |
| 20 | `scripts/phase0/validate_verification_loop.py` | new — copywriter↔verifier loop validation | `python3 scripts/phase0/validate_verification_loop.py` | 6 passed, 0 failed |
| 21 | `scripts/phase0/check_clawhub_refs.py` | new — OpenClaw reference resolution | `python3 scripts/phase0/check_clawhub_refs.py` | 19 passed, 0 failed |
| 22 | `scripts/phase0/e2e_integration.py` | **Task 5** — full-trace end-to-end | `python3 scripts/phase0/e2e_integration.py` | 14 passed, 0 failed |
| 23 | `PROJECT_STATE.md` | **Task 6** — manifest, resumable | — | — |
| 24 | `docs/BUILD_PART1_REPORT.md` | **Task 6** — this report | — | — |

Also green: `python3 -m compileall -q .` → exit 0.

### Full-suite run (final, this session)

```
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

## Deliberately deferred to Part 2

| Deferred item | Why |
|---|---|
| Reply ingestion + classification (interested / not-now / unsubscribe / bounce) and writing it back to the lead note | Part 1's scope stops at a dry-run send plus the run-log line; the prompt explicitly puts "replies" in Part 2 |
| Supervisor console | Named in the Part 2 scope |
| Monitoring (long-running health/metrics view) | Named in the Part 2 scope; Part 1 records `external_http_calls`, `attempted_provider_calls` and incident notes only |
| Final packaging of the finished product | Part 2's closing step; Part 1 exports what exists now |
| Live credentials / real transports (OmniRoute, FreeLLMAPI, Zoho, Gmail, AgentMail) | This session has no credentials of any kind, by design — the adapters are written but refused to run |
| Live suppression-list refresh, bounce/complaint handling | Belongs with the reply pipeline in Part 2; Part 1's list is a static local constant |

## Honest boundary notes

* `send_outreach(dry_run=False)` is implemented but can never succeed here: with
  no credentials it returns `status="provider_unavailable"` and logs the attempt
  in `attempted_provider_calls` while `external_http_calls` stays empty. That is
  tested behaviour, not a defect.
* The recorded graph traces come from the local engine in
  `orchestrator/graph/engine.py`; the `langgraph` package is detected and its
  `StateGraph` imported when present (`LANGGRAPH_AVAILABLE=True` observed), but
  test traces use the local engine so they are stable across package versions.
* One lead per graph run (`max_leads=1`); CSV batching across runs is a Part 2 concern.
