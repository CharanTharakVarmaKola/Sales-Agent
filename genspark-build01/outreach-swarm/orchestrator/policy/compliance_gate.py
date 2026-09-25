#!/usr/bin/env python3
"""orchestrator/policy/compliance_gate.py

Deterministic, fully offline compliance gate for the outreach swarm.

Design notes
------------
* Pure function `evaluate(lead, ...)` -> decision dict. No network, no clock
  unless a timestamp is supplied, so it is trivially unit-testable.
* The gate is the authority for "may we contact this lead at all". The sender
  (mcp-servers/email-outreach/server.py) calls it itself, and the graph calls it
  again immediately before the sender node (BUILD Task 4) — the checks are not
  duplicated in either place, both call this module.
* A root-level shim `compliance_gate.py` re-exports this module so the manifest
  command `python3 orchestrator/policy/compliance_gate.py` and the scaffold-plan
  command `python3 compliance_gate.py` both run this same code.

Self-test:  python3 orchestrator/policy/compliance_gate.py  (or --selftest)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Fields the gate reads. Anything else is ignored, never guessed.
READ_FIELDS = (
    "lead_id", "email", "unsubscribed", "country", "domain", "company",
    "pain_point", "angle", "send_count", "source",
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

#: Hard-coded suppression list (no network lookups by design).
DEFAULT_SUPPRESSED_DOMAINS: tuple[str, ...] = ("example-suppressed.test",)

#: Countries/blocks the operator wants excluded. Empty by default.
DEFAULT_BLOCKED_COUNTRIES: tuple[str, ...] = ()

MAX_SENDS_PER_LEAD = 3


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on", "unsubscribed", "opt-out"}


def evaluate(
    lead: Mapping[str, Any],
    *,
    max_sends_per_lead: int = MAX_SENDS_PER_LEAD,
    suppressed_domains: Sequence[str] = DEFAULT_SUPPRESSED_DOMAINS,
    blocked_countries: Sequence[str] = DEFAULT_BLOCKED_COUNTRIES,
    dry_run: bool = False,
    now: str | None = None,
) -> Dict[str, Any]:
    """Evaluate one lead. Returns a decision dict; never raises for bad data."""
    checks: List[Dict[str, Any]] = []

    def rule(name: str, passed: bool, detail: str) -> None:
        checks.append({"rule": name, "passed": bool(passed), "detail": detail})

    email = str(lead.get("email") or "").strip()
    lead_id = str(lead.get("lead_id") or "")
    domain = str(lead.get("domain") or "").strip().lower()
    country = str(lead.get("country") or "").strip().upper()

    rule("has_lead_id", bool(lead_id), f"lead_id={lead_id!r}")
    rule("has_email", bool(email), "email present" if email else "email missing")
    rule("email_wellformed", bool(EMAIL_RE.match(email)), f"email={email!r}")
    rule("not_unsubscribed", not _truthy(lead.get("unsubscribed")),
         f"unsubscribed={lead.get('unsubscribed')!r}")
    rule("domain_not_suppressed", domain not in {d.lower() for d in suppressed_domains},
         f"domain={domain!r}")
    rule("country_allowed", country not in {c.upper() for c in blocked_countries},
         f"country={country!r}")
    try:
        sends = int(lead.get("send_count") or 0)
    except (TypeError, ValueError):
        sends = max_sends_per_lead
    rule("send_limit_not_reached", sends < int(max_sends_per_lead),
         f"send_count={sends} < max={max_sends_per_lead}")
    rule("has_targeting_context",
         bool(str(lead.get("pain_point") or "").strip() or str(lead.get("angle") or "").strip()),
         "pain_point/angle present")

    reasons = [c["rule"] for c in checks if not c["passed"]]
    decision = "allow" if not reasons else "block"
    return {
        "gate": "compliance_gate",
        "lead_id": lead_id,
        "decision": decision,
        "reasons": reasons,
        "checks": checks,
        "dry_run": bool(dry_run),
        "evaluated_at": now,
        "policy": {
            "max_sends_per_lead": int(max_sends_per_lead),
            "suppressed_domains": list(suppressed_domains),
            "blocked_countries": list(blocked_countries),
        },
    }


def is_allowed(lead: Mapping[str, Any], **kw: Any) -> bool:
    return evaluate(lead, **kw)["decision"] == "allow"


# --------------------------------------------------------------------------
# Self-test:  python3 orchestrator/policy/compliance_gate.py
# --------------------------------------------------------------------------
def _selftest() -> int:
    cases: List[tuple[str, Dict[str, Any], str]] = [
        ("clean lead is allowed", {"lead_id": "ld-1", "email": "a@b.example", "unsubscribed": False,
                                   "domain": "b.example", "country": "UK", "pain_point": "manual work"}, "allow"),
        ("unsubscribed lead is blocked", {"lead_id": "ld-2", "email": "a@b.example", "unsubscribed": True,
                                          "domain": "b.example", "country": "UK", "pain_point": "manual work"}, "block"),
        ("string 'true' unsubscribe is blocked", {"lead_id": "ld-3", "email": "a@b.example", "unsubscribed": "TRUE",
                                                  "domain": "b.example", "country": "UK", "pain_point": "x"}, "block"),
        ("missing email is blocked", {"lead_id": "ld-4", "email": "", "unsubscribed": False,
                                      "domain": "b.example", "country": "UK", "pain_point": "x"}, "block"),
        ("malformed email is blocked", {"lead_id": "ld-5", "email": "not-an-email", "unsubscribed": False,
                                        "domain": "b.example", "country": "UK", "pain_point": "x"}, "block"),
        ("suppressed domain is blocked", {"lead_id": "ld-6", "email": "a@example-suppressed.test", "unsubscribed": False,
                                          "domain": "example-suppressed.test", "country": "UK", "pain_point": "x"}, "block"),
        ("send limit reached is blocked", {"lead_id": "ld-7", "email": "a@b.example", "unsubscribed": False,
                                           "domain": "b.example", "country": "UK", "pain_point": "x", "send_count": 3}, "block"),
        ("missing lead_id is blocked", {"lead_id": "", "email": "a@b.example", "unsubscribed": False,
                                        "domain": "b.example", "country": "UK", "pain_point": "x"}, "block"),
    ]
    passed = failed = 0
    for label, lead, expected in cases:
        got = evaluate(lead, now="2026-01-01T00:00:00Z")
        ok = got["decision"] == expected
        passed += ok
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}  -> {got['decision']} (expected {expected})"
              + ("" if ok else f"  reasons={got['reasons']}"))

    allowed = evaluate(cases[0][1], now="2026-01-01T00:00:00Z")
    blocked = evaluate(cases[1][1], now="2026-01-01T00:00:00Z")
    extra_ok = (
        allowed["decision"] == "allow"
        and allowed["reasons"] == []
        and all(c["passed"] for c in allowed["checks"])
        and blocked["reasons"] == ["not_unsubscribed"]
        and allowed["dry_run"] is False
        and set(allowed["policy"]) == {"max_sends_per_lead", "suppressed_domains", "blocked_countries"}
    )
    print(f"{'PASS' if extra_ok else 'FAIL'}  allow path has zero reasons / block path names 'not_unsubscribed'")
    passed += extra_ok
    failed += (not extra_ok)
    print(f"\ncompliance_gate.py selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="offline compliance gate")
    p.add_argument("--selftest", action="store_true", help="run the module self-test (default action)")
    args = p.parse_args(list(argv) if argv is not None else None)
    return _selftest()


if __name__ == "__main__":
    raise SystemExit(main())
