#!/usr/bin/env python3
"""Root shim: the compatibility report's file/module plan lists
``python3 compliance_gate.py``. Re-export the real module so that command and the
canonical ``python3 orchestrator/policy/compliance_gate.py`` run identical code.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestrator.policy.compliance_gate import (  # noqa: F401,E402
    DEFAULT_BLOCKED_COUNTRIES,
    DEFAULT_SUPPRESSED_DOMAINS,
    MAX_SENDS_PER_LEAD,
    evaluate,
    is_allowed,
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
