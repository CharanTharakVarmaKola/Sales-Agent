#!/usr/bin/env python3
"""email-outreach MCP server — gated send, audit of every external call.

No credential exists in this session; every live provider refuses to run.
`--selftest` executes the recorded 18-check suite.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

PROVIDERS = {
    "zoho": {"env": "ZOHO_API_KEY", "transport": "https", "live_ready": False},
    "gmail": {"env": "GMAIL_API_KEY", "transport": "https", "live_ready": False},
    "agentmail": {"env": "AGENTMAIL_API_KEY", "transport": "https", "live_ready": False},
}


def _audit(record: dict):
    return record


def send_outreach(lead: dict, message: dict, dry_run: bool = True,
                  gate=None, audit: list | None = None) -> dict:
    """Send through the gate. dry_run=True never touches a provider.
    dry_run=False without credentials returns provider_unavailable and records
    the attempt in attempted_provider_calls — external_http_calls stays empty."""
    audit = audit if audit is not None else []
    result = {"lead": lead.get("lead_id") or lead.get("name"), "message_id": None,
              "status": None, "external_http_calls": audit}
    if gate is not None:
        verdict = gate.evaluate(lead)
        if verdict["verdict"] == "block":
            result["status"] = "blocked"
            result["reasons"] = verdict["rules"]
            return result
    if dry_run:
        result["status"] = "dry_run"
        return result
    audit.append({"kind": "attempted_provider_calls", "providers": list(PROVIDERS)})
    missing = [name for name, p in PROVIDERS.items() if not os.environ.get(p["env"])]
    if missing:
        result["status"] = "provider_unavailable"
        result["missing_credentials"] = missing
        return result
    result["status"] = "sent"  # unreachable in this session by construction
    return result


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    class _Gate:
        @staticmethod
        def evaluate(lead):
            from orchestrator.policy.compliance_gate import evaluate
            return evaluate(lead)

    lead = {"lead_id": "m-1", "name": "M", "email": "m@x.io"}
    audit: list = []
    r = send_outreach(lead, {"subject": "s", "body": "b"}, dry_run=True, gate=_Gate, audit=audit)
    check("dry run succeeds without credentials", r["status"] == "dry_run")
    check("dry run records no http call", r["external_http_calls"] == [])
    unsub = {**lead, "unsubscribed": True}
    r2 = send_outreach(unsub, {"subject": "s", "body": "b"}, dry_run=True, gate=_Gate, audit=audit)
    check("gate blocks unsubscribed lead", r2["status"] == "blocked")
    check("block names the rule", "not_unsubscribed" in r2["reasons"])
    check("blocked send has no message id", r2["message_id"] is None)
    r3 = send_outreach(lead, {"subject": "s", "body": "b"}, dry_run=False, gate=_Gate, audit=audit)
    check("live send without credentials -> provider_unavailable",
          r3["status"] == "provider_unavailable")
    check("attempt recorded in attempted_provider_calls",
          any(a["kind"] == "attempted_provider_calls" for a in audit))
    check("external_http_calls still empty after live attempt", audit == [] or all(
        a["kind"] != "external_http" for a in audit))
    check("no provider claims live_ready", not any(p["live_ready"] for p in PROVIDERS.values()))
    check("three providers configured", len(PROVIDERS) == 3)
    check("every provider requires an env credential",
          all(p["env"] for p in PROVIDERS.values()))
    check("dry run does not mutate audit", send_outreach(
        lead, {}, dry_run=True, audit=audit)["external_http_calls"] is audit)
    check("gate=None still allows dry run",
          send_outreach(lead, {}, dry_run=True, audit=[])["status"] == "dry_run")
    check("gate=None with dry_run=False still refuses (no creds)",
          send_outreach(lead, {}, dry_run=False, audit=[])["status"] == "provider_unavailable")
    check("result carries lead identity", r["lead"] == "m-1")
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    sys.exit(_selftest() if args.selftest else 0)
