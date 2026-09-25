#!/usr/bin/env python3
"""validate_compliance_gate.py — independent validation of the gate."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from orchestrator.policy import compliance_gate  # noqa: E402


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    lead = {"email": "v@x.io"}
    check("clean lead allowed", compliance_gate.evaluate(lead)["verdict"] == "allow")
    for field in ("unsubscribed", "bounced", "do_not_contact", "jurisdiction_hold"):
        check(f"{field} blocks", compliance_gate.evaluate(
            {**lead, field: True})["verdict"] == "block")
    check("empty email blocks", compliance_gate.evaluate({"email": ""})["verdict"] == "block")
    check("gate exposes rule list", isinstance(
        compliance_gate.evaluate({**lead, "bounced": True})["rules"], list))
    check("gate deterministic", compliance_gate.evaluate(lead) == compliance_gate.evaluate(lead))
    check("should_send mirrors evaluate",
          compliance_gate.should_send(lead) and not compliance_gate.should_send(
              {**lead, "do_not_contact": True}))
    check("gate version pinned", compliance_gate.GATE_VERSION == "1.0.0")
    check("gate has no LLM/model field in verdict",
          "model" not in compliance_gate.evaluate(lead))
    check("force_block context honoured",
          compliance_gate.evaluate(lead, {"force_block": True})["verdict"] == "block")
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest())
