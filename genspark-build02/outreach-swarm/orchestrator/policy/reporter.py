"""reporter.py — the ONLY module allowed to write terminal frontmatter fields.

Owns TERMINAL_FRONTMATTER (the allowlist) and find_terminal_field_writes()
(the audit helper other nodes use to prove they never touch terminal fields).
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from orchestrator import obsidian_client as oc  # noqa: E402

TERMINAL_FRONTMATTER = {
    "unsubscribed", "bounced", "do_not_contact", "jurisdiction_hold",
    "reply_intent", "final_status", "send_count", "run_id",
}

# matches patch_frontmatter(<anything>, "unsubscribed"|'unsubscribed' ...)
_FM_CALL = re.compile(r"patch_frontmatter\([^,]+,\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']")


def find_terminal_field_writes(source_text: str) -> list[str]:
    """Scan python source for direct patch_frontmatter calls on terminal fields."""
    return [m.group(1) for m in _FM_CALL.finditer(source_text)
            if m.group(1) in TERMINAL_FRONTMATTER]


def set_terminal_field(lead_path: str, key: str, value, reason: str = "") -> None:
    """Write a terminal field through the allowlist. Nothing else may do this."""
    if key not in TERMINAL_FRONTMATTER:
        raise PermissionError(
            f"'{key}' is not in TERMINAL_FRONTMATTER allowlist — refusing to write")
    oc.patch_frontmatter(lead_path, key, value)
    if reason:
        oc.append_run_log(lead_path, f"terminal field {key}={value} — {reason}")


def record_run(lead_path: str, run_id: str, line: str) -> None:
    set_terminal_field(lead_path, "run_id", run_id)
    oc.append_run_log(lead_path, f"[{run_id}] {line}")


def record_send(lead_path: str, run_id: str, status: str) -> None:
    fm = oc.read_frontmatter(lead_path)
    set_terminal_field(lead_path, "send_count", int(fm.get("send_count", 0)) + 1)
    set_terminal_field(lead_path, "final_status", status)
    oc.append_run_log(lead_path, f"[{run_id}] send attempt: {status}")


def write_incident_note(lead_path: str, incident_id: str, text: str) -> None:
    oc.write_incident(lead_path, incident_id, text)


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    tmp = tempfile.mkdtemp(prefix="reporter-selftest-")
    try:
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.join(os.path.dirname(__file__), "..", "..", "vault-schema", "Templates", "Lead.md"),
            encoding="utf-8").read())
        p = oc.write_lead(vault, {"lead_id": "r-1", "name": "Rep Tester",
                                  "email": "r@t.io", "company": "Co"})
        check("allowlist contains the four compliance states",
              {"unsubscribed", "bounced", "do_not_contact", "jurisdiction_hold"}
              <= TERMINAL_FRONTMATTER)
        set_terminal_field(p, "unsubscribed", True, reason="mock unsubscribe")
        check("set_terminal_field writes an allowlisted field",
              oc.read_frontmatter(p)["unsubscribed"] is True)
        check("run-log reason line recorded", "mock unsubscribe" in oc.read_note(p))
        try:
            set_terminal_field(p, "name", "evil")
            check("non-allowlisted field raises PermissionError", False)
        except PermissionError:
            check("non-allowlisted field raises PermissionError", True)
        check("name untouched after refused write",
              oc.read_frontmatter(p)["name"] == "Rep Tester")
        record_run(p, "run-9", "test run line")
        check("record_run stamps run_id", oc.read_frontmatter(p)["run_id"] == "run-9")
        check("record_run appends log line", "[run-9] test run line" in oc.read_note(p))
        record_send(p, "run-9", "dry_run")
        fm = oc.read_frontmatter(p)
        check("record_send increments send_count and stamps final_status",
              fm["send_count"] == 1 and fm["final_status"] == "dry_run")
        write_incident_note(p, "INC-9", "mock incident")
        check("incident note written", "[INC-9] mock incident" in oc.read_note(p))
        smuggled = 'patch_frontmatter(p, "unsubscribed", True)'
        check("find_terminal_field_writes detects smuggled write",
              find_terminal_field_writes(smuggled) == ["unsubscribed"])
        clean = 'patch_frontmatter(p, "notes", "ok")'
        check("find_terminal_field_writes passes clean source",
              find_terminal_field_writes(clean) == [])
        check("ingest-style source with terminal columns is flagged",
              find_terminal_field_writes(
                  'x="run_id"; patch_frontmatter(p, "send_count", 0)') == ["send_count"])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if ("--selftest" in sys.argv or True) else 0)
