#!/usr/bin/env python3
"""validate_verification_loop.py — copywriter <-> verifier loop validation."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from orchestrator.inference.mock import (HallucinatingMockClient,  # noqa: E402
                                         MockInferenceClient)
from orchestrator.nodes import copywriter, verifier  # noqa: E402
from orchestrator import obsidian_client as oc  # noqa: E402


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    check("contract version pinned", verifier.CONTRACT_VERSION == "1.0.0")
    check("required keys are subject/body/claims",
          verifier.REQUIRED_KEYS == ("subject", "body", "claims"))
    tmp = tempfile.mkdtemp(prefix="vloop-")
    try:
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.join(os.path.dirname(__file__), "..", "..", "vault-schema", "Templates", "Lead.md"),
            encoding="utf-8").read())
        p = oc.write_lead(vault, {"lead_id": "v-1", "name": "V", "company": "Co",
                                  "email": "v@x.io", "industry": ""})
        good = copywriter.draft_lead(vault, p, MockInferenceClient())
        check("grounded draft passes the loop", good["verdict"]["verdict"] == "accept")
        bad = copywriter.draft_lead(vault, p, HallucinatingMockClient())
        check("unbacked draft rejected by the loop", bad["verdict"]["verdict"] == "reject")
        check("rejection lists reasons", len(bad["verdict"]["reasons"]) >= 1)
        check("verifier untouched module asserts claims structure",
              verifier.verify_draft({"subject": "s", "body": "b", "claims": "notalist"}
                                    )["verdict"] == "reject")
        check("empty claims list still needs required keys",
              verifier.verify_draft({"subject": "s", "body": "b", "claims": []}
                                    )["verdict"] == "accept")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        pass
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest())
