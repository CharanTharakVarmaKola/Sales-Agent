#!/usr/bin/env python3
"""check_clawhub_refs.py — prove no skill-loading path references ClawHub."""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PATTERN = re.compile(r"clawhub", re.IGNORECASE)
ALLOWED = ("scripts/phase0/check_clawhub_refs.py",
           "PROJECT_STATE.md",          # boundary list names it to forbid it
           "HANDOFF-TO-LAPTOP.md",      # handoff doc names it to forbid it
           "docs/BUILD_PROMPT_PART2.md",  # the build prompt itself names it
           "docs/BUILD_PART1_REPORT.md", "docs/BUILD_PART2_REPORT.md",
           "scripts/phase0/smoke_test_full_system.py")  # runs its own scoped boundary check


def scan():
    hits = []
    for root, _dirs, files in os.walk(ROOT):
        if ".git" in root or "__pycache__" in root or "node_modules" in root:
            continue
        for f in files:
            if not f.endswith((".py", ".md", ".sh", ".json", ".yaml", ".yml")):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel in ALLOWED:
                continue
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if PATTERN.search(line):
                    hits.append(f"{rel}:{i}")
    return hits


if __name__ == "__main__":
    hits = scan()
    print(f"clawhub references outside the checker itself: {hits or 'none'}")
    sys.exit(0 if not hits else 1)
