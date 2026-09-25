# HANDOFF-TO-LAPTOP.md

This project was built and tested entirely in a sandboxed session with **no
credentials of any kind** and **no network access to any real service**. The
following things could NOT be done here and MUST happen on the real laptop
before this system sends anything to a real person:

## 1. Obtain and enter real credentials
None of these keys exist in this session and none were requested:
- `OMNIROUTE_API_KEY` — primary inference route
- `FREELLMAPI_API_KEY` — fallback inference route
- `ZOHO_API_KEY` — Zoho mail transport
- `GMAIL_API_KEY` — Gmail transport
- `AGENTMAIL_API_KEY` — AgentMail transport

Until these are set, `send_outreach(dry_run=False)` returns
`status="provider_unavailable"` and records the attempt in
`attempted_provider_calls` without ever touching the network. That is the
tested, intended behaviour.

## 2. Run the Phase-0 live-service validation gates
Every recorded self-test ran on mocks and a local temp folder. On the laptop,
re-run the full suite from `PROJECT_STATE.md` (all commands are recorded there
with their expected results) **and then** validate the live adapters against
the real services before enabling them.

## 3. Connect to a real Obsidian vault
All tests used a local temp folder standing in for the vault
(`orchestrator/obsidian_client.py` does direct file I/O). Point
`OBSIDIAN_VAULT` at the real vault, confirm the `vault-schema/Templates/Lead.md`
template is in place, and verify concurrent-edit behaviour on the real
filesystem.

## 4. Turn off dry-run mode
Everything recorded here ran with `dry_run=True`. Only after steps 1–3 are
done and validated should `dry_run` be switched off — the compliance gate runs
immediately before every real send, and unsubscribed/bounced/do-not-contact/
jurisdiction-hold leads are hard-blocked by deterministic checks.

## 5. Honest boundary statement
**Nothing in this export has been tested against a real external service.**
No email was sent, no API was called, no vault was touched outside local temp
folders. The Part 1 code was rebuilt from its recorded specification in this
session because the original archive was not attached; all recorded self-tests
pass, but prefer the original Part 1 archive if it is available.
