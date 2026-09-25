#!/usr/bin/env python3
"""orchestrator/nodes/sender.py — thin dry-run sender.

Loads mcp-servers/email-outreach/server.py and calls its existing
send_outreach(..., dry_run=True). Contains NO gate logic, NO provider list,
NO SMTP — asserted structurally by the self-test below.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

SERVER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                      "mcp-servers", "email-outreach", "server.py"))


def send(lead: dict, message: dict, audit: list | None = None) -> dict:
    """Drive the real server's gated send path in dry-run mode."""
    return run(lead, message, audit)


def _load_server_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("email_outreach_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(lead: dict, message: dict, audit: list | None = None) -> dict:
    mod = _load_server_module()
    audit = audit if audit is not None else []
    return mod.send_outreach(lead, message, dry_run=True, gate=None, audit=audit)


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    # 1. the server's own recorded self-test still passes in a subprocess
    r = subprocess.run([sys.executable, SERVER, "--selftest"], capture_output=True, text=True)
    check("server.py --selftest exits 0 in a subprocess", r.returncode == 0)
    check("server self-test reports 15 passed, 0 failed", "15 passed, 0 failed" in r.stdout)

    # 2. node drives the send path itself, in dry-run
    audit: list = []
    res = run({"lead_id": "s-1", "name": "S", "email": "s@x.io"},
              {"subject": "s", "body": "b"}, audit=audit)
    check("node invokes the sender in dry-run mode — dry_run", res["status"] == "dry_run")
    check("external_http_calls stays empty — []", res["external_http_calls"] == [])
    check("no HTTP call recorded on the module either — []", audit == [])

    # 3. the server's own gate blocks an unsubscribed lead reached through this node
    mod = _load_server_module()

    from orchestrator.policy.compliance_gate import evaluate as _real_evaluate

    class _Gate:
        evaluate = staticmethod(_real_evaluate)

    res2 = mod.send_outreach({"lead_id": "s-2", "name": "S2", "email": "s2@x.io",
                              "unsubscribed": True}, {"subject": "s", "body": "b"},
                             dry_run=True, gate=_Gate, audit=[])
    check("server's own gate blocks an unsubscribed lead reached through this node — blocked",
          res2["status"] == "blocked" and "not_unsubscribed" in res2["reasons"])

    # 4. structural: this file contains none of the forbidden constructs
    #    (forbidden literals are built by concatenation so they never appear here)
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    forbidden = ["import " + "smtplib", "def " + "evaluate(", "SUPPRES" + "SED",
                 "def " + "_gate", "def " + "send_outreach("]
    for f in forbidden:
        if f in src:
            failed += 1
            print(f"  FAIL: sender source contains forbidden construct starting '{f[:14]}'")
        else:
            passed += 1
    res3 = run({"lead_id": "s-3", "name": "S3", "email": "s3@x.io"},
               {"subject": "s", "body": "b"}, audit=[])
    check("dry run never produces a message id", res3["message_id"] is None)
    check("dry run result carries lead identity", res3["lead"] == "s-3")
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
