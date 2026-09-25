#!/usr/bin/env python3
"""orchestrator/nodes/sender.py — BUILD Task 3.

A deliberately thin node. All compliance logic, suppression handling and
provider selection live in ``mcp-servers/email-outreach/server.py`` and
``orchestrator/policy/compliance_gate.py``; this file only invokes the server's
existing ``send_outreach`` function with ``dry_run=True`` and re-shapes the
result into graph state.

Self-test:  python3 orchestrator/nodes/sender.py --selftest
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "email-outreach" / "server.py"


def load_server() -> Any:
    """Import the hyphenated MCP server directory as a module."""
    if "outreach_server" in sys.modules:
        return sys.modules["outreach_server"]
    spec = importlib.util.spec_from_file_location("outreach_server", SERVER_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load {SERVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["outreach_server"] = module
    spec.loader.exec_module(module)
    return module


def run(
    lead: Mapping[str, Any],
    draft: Mapping[str, Any],
    *,
    dry_run: bool = True,
    outbox: Optional[str | Path] = None,
    run_id: str = "",
) -> Dict[str, Any]:
    """Invoke the existing server send function. No logic is duplicated here."""
    server = load_server()
    res = server.send_outreach(
        lead,
        str(draft.get("subject") or ""),
        str(draft.get("body") or ""),
        dry_run=dry_run,
        outbox=outbox,
        run_id=run_id,
    )
    return {
        "node": "sender",
        "lead_id": lead.get("lead_id"),
        "dry_run": bool(dry_run),
        "status": res.get("status"),
        "message_id": res.get("message_id"),
        "compliance": res.get("compliance"),
        "external_http_calls": res.get("external_http_calls", []),
        "reasons": res.get("reasons", []),
        "outbox_path": res.get("outbox_path"),
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
CLEAN_LEAD = {
    "lead_id": "ld-sender-clean", "email": "sofia@verdantgrid.example", "unsubscribed": False,
    "domain": "verdantgrid.example", "country": "IT", "industry": "energy",
    "pain_point": "grid imbalance", "angle": "automate dispatch", "send_count": 0,
}
DRAFT = {"subject": "Verdant Grid: grid imbalance", "body": "Hi Sofia,\n\nWould a call help?\n\nBest,\nSwarm\n"}


def _selftest() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    # 1. basis for the check: the sender's own existing --selftest mechanism
    proc = subprocess.run([sys.executable, str(SERVER_PATH), "--selftest"],
                          capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:] or [proc.stderr.strip()[:120]]
    check("mcp-servers/email-outreach/server.py --selftest exits 0", proc.returncode == 0, tail[0])

    server = load_server()
    check("server exposes external_http_calls and it starts empty", getattr(server, "external_http_calls", None) == [])

    with tempfile.TemporaryDirectory(prefix="sender-selftest-") as tmp:
        res = run(CLEAN_LEAD, DRAFT, dry_run=True, outbox=Path(tmp))
        check("node invokes the sender in dry-run mode", res["status"] == "dry_run" and res["dry_run"] is True, res["status"])
        check("message id returned by the server", bool(res["message_id"]), str(res["message_id"]))
        check("external_http_calls stays empty", res["external_http_calls"] == [], str(res["external_http_calls"]))
        check("no HTTP call recorded on the module either", server.external_http_calls == [], str(server.external_http_calls))
        check("outbox artifact written by the server, not by this node",
              bool(res["outbox_path"]) and Path(res["outbox_path"]).exists(), str(res["outbox_path"]))
        check("compliance verdict travels back with the send result",
              res["compliance"]["decision"] == "allow", str(res["compliance"]["reasons"]))

        blocked = run({**CLEAN_LEAD, "lead_id": "ld-sender-blocked", "unsubscribed": True}, DRAFT, dry_run=True, outbox=Path(tmp))
        check("server's own gate blocks an unsubscribed lead reached through this node",
              blocked["status"] == "blocked", blocked["status"])
        check("no external call on the blocked path", blocked["external_http_calls"] == [])

    src = Path(__file__).read_text(encoding="utf-8")
    # names are assembled so these assertions cannot match their own source text
    banned = ("import " + "smt" + "plib", "def " + "evaluate(", "SUPPRESS" + "ED", "def " + "_gate",
              "def " + "send_outreach(")
    for name in banned:
        check(f"node does not re-implement server logic ({name})", name not in src)

    print(f"\nsender.py selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="thin sender node (dry-run)")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return _selftest()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
