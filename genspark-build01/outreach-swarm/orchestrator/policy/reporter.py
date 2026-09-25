#!/usr/bin/env python3
"""orchestrator/policy/reporter.py

Sole owner of the TERMINAL frontmatter section of a lead note.

`TERMINAL_FRONTMATTER` is the allowlist referenced by the BUILD spec (Task 1 /
Task 5). No node other than this reporter may write those fields; the ingest
node imports `TERMINAL_FRONTMATTER` and `find_terminal_field_writes()` from
here instead of re-implementing the permission check.

Contract used by the rest of the swarm:
    find_terminal_field_writes(updates)  -> sorted list of offending keys
    assert_no_terminal_writes(updates)   -> raises PermissionError on violation
    new_run_id(prefix)                   -> 'run-YYYYmmddTHHMMSSZ-xxxxxx'
    record_outcome(vault, lead, **)      -> terminal fields + run-log line
    write_incident(vault, ...)           -> vault/_incidents/<slug>.md

Self-test:  python3 orchestrator/policy/reporter.py --selftest
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import obsidian_client as oc  # noqa: E402

# --------------------------------------------------------------------------
# Field ownership
# --------------------------------------------------------------------------
TERMINAL_FRONTMATTER = frozenset(
    {
        "run_id",
        "last_contacted_at",
        "send_count",
        "compliance_verdict",
        "delivery_status",
        "final_status",
    }
)

#: Everything ingest.py and the copywriter are allowed to write.
LEAD_WRITABLE_FRONTMATTER = frozenset(
    {
        "lead_id",
        "name",
        "title",
        "company",
        "domain",
        "email",
        "country",
        "industry",
        "pain_point",
        "angle",
        "source",
        "source_row",
        "unsubscribed",
        "status",
        "ingested_at",
    }
)


def find_terminal_field_writes(updates: Mapping[str, Any]) -> List[str]:
    """Return the sorted terminal keys present in an update mapping."""
    return sorted(k for k in updates if k in TERMINAL_FRONTMATTER)


def assert_no_terminal_writes(updates: Mapping[str, Any], *, who: str = "caller") -> None:
    """Raise PermissionError if `updates` touches any terminal field."""
    bad = find_terminal_field_writes(updates)
    if bad:
        raise PermissionError(
            f"{who} attempted to write terminal frontmatter fields {bad}; "
            f"only reporter.py may write {sorted(TERMINAL_FRONTMATTER)}"
        )


def new_run_id(prefix: str = "run") -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------------
# Vault writes (delegated to obsidian_client.py — direct file I/O)
# --------------------------------------------------------------------------
def _note(vault: Path, lead: Any) -> Path:
    return oc.resolve_note(vault, lead)


def record_outcome(
    vault: Path,
    lead: Any,
    *,
    run_id: str,
    compliance_verdict: str,
    delivery_status: str,
    final_status: str,
    stage: str = "sender",
    detail: str = "",
    extra: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Write the terminal section of a lead note and append one run-log line.

    This is the only place in the project that writes TERMINAL_FRONTMATTER.
    """
    path = _note(vault, lead)
    fm = oc.read_frontmatter(vault, lead)
    send_count = int(fm.get("send_count") or 0)
    if delivery_status == "dry_run":
        # dry runs must not inflate the real send counter
        send_count = send_count
    elif delivery_status in {"sent", "delivered"}:
        send_count += 1

    updates = {
        "run_id": run_id,
        "last_contacted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "send_count": send_count,
        "compliance_verdict": compliance_verdict,
        "delivery_status": delivery_status,
        "final_status": final_status,
    }
    if extra:
        oc.assert_not_terminal_free(extra)
    changed = oc.patch_frontmatter(vault, lead, updates)

    line = (
        f"- {updates['last_contacted_at']} | run={run_id} | stage={stage} | "
        f"compliance={compliance_verdict} | delivery={delivery_status} | "
        f"final={final_status}" + (f" | {detail}" if detail else "")
    )
    log_path = append_run_log(vault, lead, line)
    return {"note": str(path), "changed": changed, "log": str(log_path), "terminal": updates}


def append_run_log(vault: Path, lead: Any, line: str) -> Path:
    """Append a single run-log line to the lead note's '## Run log' section."""
    path = _note(vault, lead)
    if not line.startswith("-"):
        line = f"- {line}"
    return oc.patch_section(vault, lead, "## Run log", line, position="end_of_section")


def write_incident(
    vault: Path,
    title: str,
    body: str,
    *,
    severity: str = "warning",
    run_id: str = "",
) -> Path:
    return oc.write_incident(vault, title, body, severity=severity, run_id=run_id)


# --------------------------------------------------------------------------
# Self-test:  python3 orchestrator/policy/reporter.py --selftest
# --------------------------------------------------------------------------
def _selftest() -> int:
    checks: List[str] = []
    fails: List[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        (checks if ok else fails).append(f"{label}{(' — ' + detail) if detail else ''}")

    # 1. allowlist content
    check(
        "TERMINAL_FRONTMATTER allowlist present",
        TERMINAL_FRONTMATTER == frozenset(
            {"run_id", "last_contacted_at", "send_count", "compliance_verdict",
             "delivery_status", "final_status"}
        ),
        ",".join(sorted(TERMINAL_FRONTMATTER)),
    )
    # 2. terminal-write detection
    offenders = find_terminal_field_writes({"name": "x", "run_id": "r", "send_count": 1})
    check("find_terminal_field_writes detects violators", offenders == ["run_id", "send_count"], str(offenders))
    # 3. guard raises
    raised = False
    try:
        assert_no_terminal_writes({"final_status": "sent"}, who="selftest")
    except PermissionError:
        raised = True
    check("assert_no_terminal_writes raises PermissionError", raised)
    # 4. run id shape
    rid = new_run_id("demo")
    check("new_run_id shape", rid.startswith("demo-") and len(rid) > 20, rid)

    # 5. end-to-end write into a temp vault
    with tempfile.TemporaryDirectory(prefix="reporter-selftest-") as tmp:
        vault = Path(tmp) / "vault"
        oc.ensure_vault(vault)
        lead = oc.write_lead(
            vault,
            {
                "lead_id": "ld-selftest",
                "name": "Ada Lovelace",
                "title": "CTO",
                "company": "Analytical Engines Ltd",
                "domain": "analytical.example",
                "email": "ada@analytical.example",
                "country": "UK",
                "industry": "industrial software",
                "pain_point": "manual reporting",
                "angle": "cut reporting time",
                "source": "selftest",
                "source_row": 1,
                "unsubscribed": False,
                "status": "new",
            },
        )
        res = record_outcome(
            vault, "ld-selftest", run_id=new_run_id("demo"),
            compliance_verdict="allow", delivery_status="dry_run",
            final_status="dry_run_complete", detail="selftest run",
        )
        fm = oc.read_frontmatter(vault, "ld-selftest")
        check("terminal fields written by reporter",
              fm.get("compliance_verdict") == "allow" and fm.get("final_status") == "dry_run_complete",
              str({k: fm.get(k) for k in sorted(TERMINAL_FRONTMATTER)}))
        check("dry-run does not inflate send_count", int(fm.get("send_count") or 0) == 0, str(fm.get("send_count")))
        note_text = oc.read_note(vault, "ld-selftest")
        check("run-log line appended", "stage=sender" in note_text, "stage=sender present in note")
        check("return payload lists changed keys", isinstance(res["changed"], list) and res["changed"], str(res["changed"]))

        inc = write_incident(vault, "selftest incident", "body text", severity="info", run_id="run-x")
        check("write_incident produced a file", inc.exists(), inc.name)

    for c in checks:
        print(f"PASS  {c}")
    for f in fails:
        print(f"FAIL  {f}")
    print(f"\nreporter.py selftest: {len(checks)} passed, {len(fails)} failed")
    return 1 if fails else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="reporter policy module")
    p.add_argument("--selftest", action="store_true", help="run the module self-test")
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return _selftest()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
