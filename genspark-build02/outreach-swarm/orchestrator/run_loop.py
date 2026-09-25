#!/usr/bin/env python3
"""orchestrator/run_loop.py — the orchestrator's main run loop.

This is where Paperclip (heartbeat.py) and the watchdog (watchdog.py) are
actually CALLED from — they are not standalone scripts: every batch run passes
through run_batch(), which heartbeats the adapter and routes any failed agent
run through watchdog.check(). Dedupe is inherited from the adapter, so one
failed run yields exactly one incident note and one watchdog notification.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def run_batch(app, leads: list[dict], vault: str, adapter, watchdog,
              run_id: str = "batch-1") -> list[dict]:
    """Run a batch of leads through the compiled graph, with monitoring wired in."""
    results = []
    adapter.heartbeat(run_id)
    for i, lead in enumerate(leads):
        state = {"lead": lead, "run_id": f"{run_id}-lead-{i + 1}",
                 "external_http_calls": []}
        try:
            result = app.invoke(state)
            adapter.record(run_id, "agent_run_complete",
                           {"lead": lead.get("lead_id"), "trace": result.get("_visited")})
            if result.get("halt_node") == "compliance":
                watchdog.check(result["lead_note"], run_id,
                               f"blocked-{lead.get('lead_id')}",
                               f"lead {lead.get('lead_id')} blocked at compliance: "
                               f"{result.get('halt_reasons')}")
            results.append(result)
        except Exception as exc:  # failed agent run -> exactly one incident
            note = state.get("lead_note") or _ensure_note(vault, lead)
            fired = watchdog.check(note, run_id, f"failure-{lead.get('lead_id')}",
                                   f"agent run failed for {lead.get('lead_id')}: {exc}")
            adapter.record(run_id, "agent_run_failed",
                           {"lead": lead.get("lead_id"), "error": str(exc),
                            "watchdog_fired": fired})
            results.append({"lead": lead.get("lead_id"), "error": str(exc),
                            "watchdog_fired": fired})
    return results


def _ensure_note(vault: str, lead: dict) -> str:
    from orchestrator import obsidian_client as oc
    lead = dict(lead)
    lead.setdefault("lead_id", lead.get("name", "lead").replace(" ", "-").lower())
    return oc.write_lead(vault, lead)


def _selftest() -> int:
    """Task 3 self-test: one failed agent run -> exactly one incident note,
    exactly one watchdog notification — reusing the adapter's dedupe logic."""
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    import tempfile
    import shutil
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "paperclip_heartbeat",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                     "paperclip-adapter", "heartbeat.py")))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    PaperclipAdapter = _mod.PaperclipAdapter
    from watchdog.watchdog import Watchdog
    from orchestrator import obsidian_client as oc

    tmp = tempfile.mkdtemp(prefix="runloop-")
    try:
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                         "vault-schema", "Templates", "Lead.md")),
            encoding="utf-8").read())
        p = oc.write_lead(vault, {"lead_id": "rl-1", "name": "Run Loop", "company": "Co",
                                  "email": "r@co.io"})
        adapter = PaperclipAdapter(os.path.join(tmp, "paperclip.json"))
        wd = Watchdog(adapter)

        fired1 = wd.check(p, "agent-7", "explode", "mock agent failure")
        check("first failed run fires exactly one watchdog notification", fired1 == 1)
        fired2 = wd.check(p, "agent-7", "explode", "mock agent failure")
        check("duplicate incident (same agent+key) fires nothing — dedupe holds",
              fired2 == 0)
        fired3 = wd.check(p, "agent-7", "explode", "mock agent failure")
        check("third identical attempt still deduped to zero", fired3 == 0)
        check("watchdog alert count is exactly 1", wd.alert_count() == 1)
        check("exactly one incident note written to the lead note",
              oc.read_note(p).count("INC-agent-7-explode") == 1)
        check("dedupe reuses the adapter's existing logic (no reimplementation here)",
              adapter.emit_incident(p, "agent-7", "explode", "x") is None)

        # wiring proof: run_batch actually calls heartbeat + watchdog
        class _ExplodingApp:
            def invoke(self, state):
                state["lead_note"] = p
                raise RuntimeError("mock failure inside graph run")

        results = run_batch(_ExplodingApp(), [{"lead_id": "rl-1", "name": "Run Loop"}],
                            vault, adapter, wd, run_id="batch-9")
        check("run_batch routes the failed graph run through the watchdog",
              results[0].get("watchdog_fired") == 1 and wd.alert_count() == 2)
        check("run_batch heartbeats the adapter per batch",
              any(r["event"] == "heartbeat" and r["agent_id"] == "batch-9"
                  for r in adapter.records()))
        check("adapter records the failure event with lead identity",
              any(r["event"] == "agent_run_failed" and r["payload"]["lead"] == "rl-1"
                  for r in adapter.records()))
        check("adapter persists records to its store",
              os.path.exists(os.path.join(tmp, "paperclip.json")))
        check("watchdog wired signature matches run loop usage",
              callable(getattr(wd, "check", None)) and callable(getattr(adapter, "heartbeat", None)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
