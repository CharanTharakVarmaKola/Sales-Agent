# AGENTS.md — agent context pointer (Sales Pipeline)

First-party scope (default context): `orchestrator/`, `dashboard/`,
`tests/`, `scripts/`, `migrations/`, `docs/`, `ARCHITECTURE.md`,
`CHANGE-NOTES.md`, `.env.example`, `pytest.ini`.

EXCLUDE from default context: `vendor/`, `reference/`, `archive/`,
`genspark-build02/`, `Finding_B_Phase/`, `.git/`, `__pycache__/`.

Standing laws (see `ARCHITECTURE.md` — canonical):

- `CHANGE-NOTES.md` is append-only; never rewrite history.
- Single-writer `Store` owns every sqlite3 connection
  (`orchestrator/store.py`); no raw sqlite3 elsewhere.
- Pause-first inference: catch `InferencePaused` by name before any
  broad except; single clock `orchestrator/timeutil.utcnow`.
- Redaction-before-transact on ALL live paths (append-only chain).
- Kill-switch `OUTREACH_LIVE` — uncached reads, snapshot-per-run.
- Personas-as-data in the `agents` table, never hardcoded strings.

Per-task budget: read `orchestrator/posture.py` + the relevant
`docs/parity-*.md` + the touched module — not the whole repo.
Python project: verify with `python -m pytest tests
dashboard/test_dashboard.py -q`. No `tsc`/`eslint`/`prettier` targets
exist in first-party code.
