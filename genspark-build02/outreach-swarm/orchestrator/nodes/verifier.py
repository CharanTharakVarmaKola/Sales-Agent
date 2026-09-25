"""verifier.py — FROZEN scaffold. No task in Part 1 or Part 2 modifies this file.

Every claim-bearing agent draft must pass verify_draft() before acceptance.
"""
from __future__ import annotations

CONTRACT_VERSION = "1.0.0"
REQUIRED_KEYS = ("subject", "body", "claims")


def verify_draft(draft: dict) -> dict:
    """Validate a copywriter draft against the frozen contract.

    Checks: contract keys present; every claim carries at least one evidence
    ref; no numeric assertion appears without an evidence ref.
    Returns {'verdict': 'accept'|'reject', 'reasons': [...], 'checks': [...]}.
    """
    reasons = []

    def check(name, ok):
        if not ok:
            reasons.append(name)
        return name

    checks = [
        check("draft_has_required_keys", all(k in draft for k in REQUIRED_KEYS)),
        check("subject_is_string", isinstance(draft.get("subject"), str)),
        check("body_is_string", isinstance(draft.get("body"), str)),
    ]
    claims = draft.get("claims", [])
    checks.append(check("claims_is_list", isinstance(claims, list)))
    for i, c in enumerate(claims if isinstance(claims, list) else []):
        refs = c.get("evidence_refs") if isinstance(c, dict) else None
        checks.append(check("claims_have_evidence_refs", bool(refs)))
        if isinstance(c, dict):
            body_nums = [w for w in str(c.get("text", "")).split() if any(ch.isdigit() for ch in w)]
            checks.append(check("no_unbacked_numeric_assertions",
                                bool(refs) or not body_nums))
    return {"verdict": "reject" if reasons else "accept",
            "reasons": list(dict.fromkeys(reasons)), "checks": checks}


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    ok = verify_draft({"subject": "s", "body": "b", "claims": [
        {"text": "grew 40% with peer firms",
         "evidence_refs": ["industries/SaaS.md#Evidence"]}]})
    check("grounded draft accepted", ok["verdict"] == "accept" and ok["reasons"] == [])
    bad = verify_draft({"subject": "s", "body": "b", "claims": [{"text": "we 3x'd clients"}]})
    check("unbacked claim rejected", bad["verdict"] == "reject")
    check("rejection names the missing-evidence check",
          "claims_have_evidence_refs" in bad["reasons"])
    check("rejection names unbacked numbers",
          "no_unbacked_numeric_assertions" in bad["reasons"])
    check("contract version pinned", CONTRACT_VERSION == "1.0.0")
    missing = verify_draft({"subject": "s", "body": "b"})
    check("missing required key rejected", missing["verdict"] == "reject")
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
