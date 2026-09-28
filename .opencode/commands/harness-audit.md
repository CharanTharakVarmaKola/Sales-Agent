---
description: Audit agent harness configuration quality (config-only, no product rewrites)
---

# Harness Audit

Score the agent harness 0-10 per category (max 50): hooks/guards,
evals/tests, routing/orchestration, context/compactness, safety/compliance.

Inventory (first-party only):

- `ARCHITECTURE.md` standing rules + `CHANGE-NOTES.md` (append-only)
- `docs/parity-*.md` contracts + checklists
- `orchestrator/posture.py`, `orchestrator/inference/`,
  `orchestrator/live.py`, `orchestrator/graph.py`, `orchestrator/consumer.py`
- `skills/agentmail-*/SKILL.md`
- `dashboard/app.py`, `dashboard/test_dashboard.py`, `tests/`
- `.env.example` vs `orchestrator/posture.py` CHANNELS diff
- `.opencode/agents/` frontmatter

Rules:

- Config/doc proposals only — never rewrite product code here.
- Small, reversible, cross-platform (Windows PowerShell + POSIX sh).
- Compatible with Claude Code, Cursor, OpenCode, Codex — no fragile quoting.
- Validate with `python -m pytest tests dashboard/test_dashboard.py -q`.

Output: baseline scores + top_actions, proposed changes as action objects
`{id, area, change, reversibility, compat, expected_delta}`, projected
deltas (same keys), remaining_risks list.
