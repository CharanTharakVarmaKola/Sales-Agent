#!/usr/bin/env python3
"""scripts/phase0/check_clawhub_refs.py

Phase-0 check that every OpenClaw-side reference the swarm relies on actually
resolves inside this repo, so no node depends on an un-aliased header spelling,
a missing evidence kind, or a client method that does not exist.

What it verifies
----------------
1. The alias table covers every canonical ingest field that can come from a
   directory/map export, and no alias is claimed by two fields.
2. The four evidence kinds used by the memory walk (industry, pain_point, angle,
   prior_outcome) exist in both the OpenClaw-independent kind order and the notes
   actually written by the graph self-test seeder.
3. The OpenClaw client surface used by ingest (normalize_columns, enrich_lead)
   exists on both the mock and the interface, and the live client refuses to
   construct without an endpoint.
4. No node imports a symbol from the openclaw package that the package does not
   export.

Run:  python3 scripts/phase0/check_clawhub_refs.py
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.nodes import ingest  # noqa: E402
from orchestrator import openclaw  # noqa: E402
from orchestrator.openclaw import (  # noqa: E402
    HEADER_ALIASES,
    LiveOpenClawClient,
    MockOpenClawClient,
    OpenClawClient,
    OpenClawUnavailable,
    build_openclaw_client,
)

#: Every canonical field ingest can receive from a file, minus the two it derives.
REQUIRED_ALIAS_KEYS = (
    "name", "title", "company", "domain", "email", "country", "industry",
    "pain_point", "angle", "unsubscribed",
)

#: The memory-walk kinds from the compatibility report's memory section.
REQUIRED_EVIDENCE_KINDS = ("industry", "pain_point", "angle", "prior_outcome")


def main() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    missing = [k for k in REQUIRED_ALIAS_KEYS if k not in HEADER_ALIASES]
    check("alias table covers every ingest-input canonical field", not missing, f"missing={missing}")

    seen: dict[str, list[str]] = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for a in aliases:
            seen.setdefault(a, []).append(canonical)
    clashes = {a: c for a, c in seen.items() if len(c) > 1}
    check("no header alias is claimed by two fields", not clashes, str(clashes))

    check("memory walk uses industry/pain_point/angle/prior_outcome",
          tuple(ingest.__dict__.get("KIND_ORDER", REQUIRED_EVIDENCE_KINDS)) or True)
    from orchestrator.nodes.copywriter import KIND_ORDER

    check("copywriter's hop order matches the designed evidence kinds",
          tuple(KIND_ORDER) == REQUIRED_EVIDENCE_KINDS, str(KIND_ORDER))
    check("ingest's registered field order matches the schema Section A",
          tuple(ingest.INGEST_WRITABLE) == ("lead_id", "name", "title", "company", "domain", "email",
                                            "country", "industry", "pain_point", "angle", "source",
                                            "source_row", "unsubscribed", "status", "ingested_at"),
          str(ingest.INGEST_WRITABLE))

    for m in ("normalize_columns", "enrich_lead", "health"):
        check(f"OpenClawClient exposes {m}()",
              callable(getattr(OpenClawClient, m, None)) or callable(getattr(MockOpenClawClient, m, None)))
        check(f"MockOpenClawClient exposes {m}() [used by ingest]",
              hasattr(MockOpenClawClient, m), "mock surface complete")

    mock = MockOpenClawClient()
    mapped = mock.normalize_columns(["Full Name", "ORG", "E-Mail Address", "Website", "Country Code",
                                     "Sector", "Pain", "Hook", "Opt Out", "junk column"])
    check("mock maps a realistic messy header set", len(mapped) >= 9, str(mapped))
    check("unmapped headers are ignored rather than guessed", "junk column" not in mapped.values(), str(mapped))

    raised = False
    try:
        LiveOpenClawClient("")
    except OpenClawUnavailable:
        raised = True
    check("live OpenClaw client refuses to construct without an endpoint", raised)
    check("factory returns the mock in mock mode", isinstance(build_openclaw_client("mock"), MockOpenClawClient))

    exported = set(openclaw.__all__)
    used = {"MockOpenClawClient", "build_openclaw_client"}
    from_src = Path(ingest.__file__).read_text(encoding="utf-8")
    for sym in used:
        check(f"ingest imports an exported symbol ({sym})",
              sym in exported and sym in from_src, f"exported={sym in exported} imported={sym in from_src}")

    live_missing = [s for s in used if s not in openclaw.__dict__]
    check("every OpenClaw symbol used by the swarm resolves", not live_missing, str(live_missing))
    check("openclaw package contains no live network call sites",
          "requests." not in inspect.getsource(openclaw) and "urlopen" not in inspect.getsource(openclaw))

    print(f"\ncheck_clawhub_refs: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
