"""compliance_gate.py — deterministic, offline send gate.

Plain field checks only. No LLM, no network, no judgment calls.
Run directly to execute the self-test:  python3 orchestrator/policy/compliance_gate.py
"""
from __future__ import annotations

import argparse
import os
import sys

GATE_VERSION = "1.0.0"

_RULES = (
    ("not_unsubscribed", lambda fm: not bool(fm.get("unsubscribed", False))),
    ("not_bounced", lambda fm: not bool(fm.get("bounced", False))),
    ("not_do_not_contact", lambda fm: not bool(fm.get("do_not_contact", False))),
    ("not_jurisdiction_hold", lambda fm: not bool(fm.get("jurisdiction_hold", False))),
    ("has_email", lambda fm: bool(str(fm.get("email", "")).strip())),
)


def evaluate(lead: dict, context: dict | None = None) -> dict:
    """Return {'verdict': 'allow'|'block', 'rules': [...], 'reasons': [...]}."""
    context = context or {}
    if context.get("force_block"):
        return {"verdict": "block", "rules": ["force_block"], "reasons": ["forced in context"]}
    failed = [name for name, ok in _RULES if not ok(lead)]
    return {"verdict": "block" if failed else "allow",
            "rules": failed,
            "reasons": [f"failed rule: {r}" for r in failed]}


def should_send(lead: dict) -> bool:
    return evaluate(lead)["verdict"] == "allow"


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    clean = {"email": "a@b.io"}
    r = evaluate(clean)
    check("clean lead is allowed", r["verdict"] == "allow")
    check("clean lead fails no rules", r["rules"] == [])
    check("verdict dict exposes rules list", isinstance(r["rules"], list))
    for field in ("unsubscribed", "bounced", "do_not_contact", "jurisdiction_hold"):
        rr = evaluate({**clean, field: True})
        check(f"{field}=True is blocked", rr["verdict"] == "block")
        check(f"{field} block names its rule", f"not_{field}" in rr["rules"])
    check("missing email is blocked", evaluate({"email": ""})["verdict"] == "block")
    check("gate is deterministic (same input, same verdict)",
          evaluate(clean) == evaluate(clean))
    check("gate never mentions an LLM (no model field in verdict)",
          "model" not in r and "llm" not in str(r).lower())
    check("should_send agrees with evaluate", should_send(clean) and not should_send(
        {**clean, "unsubscribed": True}))
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv or "--help" not in sys.argv else None
    sys.exit(_selftest())
