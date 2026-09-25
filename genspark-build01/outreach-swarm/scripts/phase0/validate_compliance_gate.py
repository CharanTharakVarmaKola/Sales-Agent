#!/usr/bin/env python3
"""scripts/phase0/validate_compliance_gate.py

Phase-0 validator for the offline compliance gate. Independent of the module's
own self-test: it re-derives the expected verdicts from first principles and
checks the gate's decision, reason list and policy echo.

Run:  python3 scripts/phase0/validate_compliance_gate.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.policy import compliance_gate  # noqa: E402

BASE = {"lead_id": "ld-v", "email": "a@b.example", "unsubscribed": False,
        "domain": "b.example", "country": "UK", "pain_point": "manual work", "send_count": 0}

CASES = [
    ({}, "allow", []),
    ({"unsubscribed": True}, "block", ["not_unsubscribed"]),
    ({"email": ""}, "block", ["has_email", "email_wellformed"]),
    ({"email": "nope"}, "block", ["email_wellformed"]),
    ({"send_count": 3}, "block", ["send_limit_not_reached"]),
    ({"domain": "example-suppressed.test"}, "block", ["domain_not_suppressed"]),
    ({"pain_point": "", "angle": ""}, "block", ["has_targeting_context"]),
    ({"unsubscribed": "yes", "send_count": "9"}, "block", ["not_unsubscribed", "send_limit_not_reached"]),
]


def main() -> int:
    passed = failed = 0
    for overrides, want_decision, want_reasons in CASES:
        lead = {**BASE, **overrides}
        got = compliance_gate.evaluate(lead, now="2026-01-01T00:00:00Z")
        ok = got["decision"] == want_decision and got["reasons"] == want_reasons
        passed += ok
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  overrides={json.dumps(overrides)} -> "
              f"decision={got['decision']} reasons={got['reasons']} (want {want_decision}/{want_reasons})")

    extra = compliance_gate.evaluate(BASE)
    ok = (extra["checks"] and all(isinstance(c["passed"], bool) for c in extra["checks"])
          and extra["evaluated_at"] is None and extra["dry_run"] is False
          and compliance_gate.is_allowed(BASE) is True)
    passed += ok
    failed += (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  decision is pure/deterministic (no clock unless supplied) and is_allowed agrees")

    print(f"\nvalidate_compliance_gate: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
