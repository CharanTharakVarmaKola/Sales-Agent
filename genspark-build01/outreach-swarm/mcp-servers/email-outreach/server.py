#!/usr/bin/env python3
"""mcp-servers/email-outreach/server.py — MCP-style email-outreach tool server.

This module owns the *only* code path that can put a message on the wire, and in
this session that path is provably never taken:

* every send goes through the deterministic compliance gate in
  ``orchestrator/policy/compliance_gate.py`` (this file does not re-implement
  any gate logic, it calls it);
* ``external_http_calls`` is a module-level audit list that a real provider
  adapter would append to. It must stay empty for every test in this repo, and
  the self-test asserts that;
* the Zoho / Gmail / AgentMail adapters below are declared as *configuration*
  only. With no credentials present they raise ``ProviderUnavailable`` and the
  attempt is recorded in ``attempted_provider_calls`` — never in
  ``external_http_calls``, because no request is made.

Self-test:  python3 mcp-servers/email-outreach/server.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.policy import compliance_gate  # noqa: E402

SERVER_NAME = "email-outreach"
SERVER_VERSION = "0.1.0-part1"

#: Audit list of every real outbound HTTP call made by this process.
#: Immutable fact for the self-test: it stays empty in this session.
external_http_calls: List[Dict[str, Any]] = []

#: Attempts that were refused before any HTTP call (no credentials / disabled).
attempted_provider_calls: List[Dict[str, Any]] = []


class ProviderUnavailable(RuntimeError):
    """Raised when a live provider adapter is asked to send without credentials."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    transport: str
    api_key_env: str
    from_env: str
    notes: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env)

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "api_key_env": self.api_key_env,
            "api_key_present": bool(self.api_key),
            "from_env": self.from_env,
            "notes": self.notes,
        }


#: PRIMARY transport in the design, FALLBACK is AgentMail.
PROVIDERS: Dict[str, ProviderSpec] = {
    "zoho": ProviderSpec("zoho", "smtp-less REST", "ZOHO_API_KEY", "OUTREACH_FROM",
                         "primary provider; REST only, no SMTP anywhere in this project"),
    "gmail": ProviderSpec("gmail", "Gmail API", "GMAIL_API_KEY", "OUTREACH_FROM",
                          "secondary provider when zoho is unavailable"),
    "agentmail": ProviderSpec("agentmail", "REST", "AGENTMAIL_API_KEY", "OUTREACH_FROM",
                              "fallback provider"),
}
DEFAULT_PROVIDER = "zoho"


def describe_providers() -> Dict[str, Any]:
    return {"default": DEFAULT_PROVIDER, "providers": {k: v.describe() for k, v in PROVIDERS.items()}}


# ---------------------------------------------------------------------------
# Delivery (dry-run is the only exercised path)
# ---------------------------------------------------------------------------
def _outbox_path(outbox: Optional[str | os.PathLike[str]], lead_id: str) -> Optional[Path]:
    if not outbox:
        return None
    p = Path(outbox)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{lead_id}-{uuid.uuid4().hex[:8]}.json"


def _dispatch_live(provider: ProviderSpec, message: Mapping[str, Any]) -> Dict[str, Any]:
    """Real adapter entry point. Refuses to run without credentials."""
    attempted_provider_calls.append(
        {"provider": provider.name, "at": time.time(), "reason": "no credentials in this session"}
    )
    raise ProviderUnavailable(
        f"{provider.name} adapter is configured but disabled: {provider.api_key_env} is unset, "
        "so no request was made and external_http_calls stays empty"
    )


def send_outreach(
    lead: Mapping[str, Any],
    subject: str,
    body: str,
    *,
    dry_run: bool = True,
    provider: str = DEFAULT_PROVIDER,
    outbox: Optional[str | os.PathLike[str]] = None,
    run_id: str = "",
) -> Dict[str, Any]:
    """Compliance-gated send. ``dry_run=True`` never touches a provider."""
    rid = run_id or f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    spec = PROVIDERS.get(provider)
    if spec is None:
        return {"status": "error", "error": f"unknown provider {provider!r}", "run_id": rid,
                "external_http_calls": list(external_http_calls)}

    decision = compliance_gate.evaluate(lead, dry_run=dry_run, now=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    base = {
        "server": SERVER_NAME,
        "run_id": rid,
        "provider": provider,
        "dry_run": bool(dry_run),
        "compliance": decision,
        "external_http_calls": list(external_http_calls),
    }
    if decision["decision"] != "allow":
        return {**base, "status": "blocked", "reasons": decision["reasons"], "message_id": None}

    message = {
        "to": lead.get("email"),
        "subject": subject,
        "body": body,
        "lead_id": lead.get("lead_id"),
        "provider": provider,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if dry_run:
        path = _outbox_path(outbox, str(lead.get("lead_id") or "lead"))
        if path:
            path.write_text(json.dumps(message, indent=2), encoding="utf-8")
        return {
            **base,
            "status": "dry_run",
            "message_id": f"dry-{uuid.uuid4().hex[:12]}",
            "outbox_path": str(path) if path else None,
            "external_http_calls": list(external_http_calls),
        }

    try:
        sent = _dispatch_live(spec, message)
        return {**base, "status": "sent", "message_id": sent.get("id"), "external_http_calls": list(external_http_calls)}
    except ProviderUnavailable as exc:
        return {**base, "status": "provider_unavailable", "error": str(exc), "message_id": None,
                "external_http_calls": list(external_http_calls)}


# ---------------------------------------------------------------------------
# MCP tool descriptors (mirrors a real MCP server's tool list)
# ---------------------------------------------------------------------------
def tool_specs() -> List[Dict[str, Any]]:
    return [
        {
            "name": "send_outreach",
            "description": "Send (or dry-run) one compliance-gated outreach message to a single lead.",
            "inputSchema": {
                "type": "object",
                "required": ["lead", "subject", "body"],
                "properties": {
                    "lead": {"type": "object"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                    "provider": {"type": "string", "enum": sorted(PROVIDERS)},
                },
            },
        },
        {
            "name": "check_compliance",
            "description": "Evaluate one lead against the offline compliance gate without sending.",
            "inputSchema": {"type": "object", "required": ["lead"], "properties": {"lead": {"type": "object"}}},
        },
        {
            "name": "describe_providers",
            "description": "Report provider configuration (never returns secrets).",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
CLEAN_LEAD = {
    "lead_id": "ld-server-clean",
    "email": "grace@navy.example",
    "unsubscribed": False,
    "domain": "navy.example",
    "country": "US",
    "industry": "defense",
    "pain_point": "manual reports",
    "angle": "automate reporting",
    "send_count": 0,
}
BLOCKED_LEAD = {**CLEAN_LEAD, "lead_id": "ld-server-blocked", "unsubscribed": True}


def _selftest() -> int:
    import tempfile

    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="smtp-server-selftest-") as tmp:
        # 1. clean lead, dry run
        res = send_outreach(CLEAN_LEAD, "Subject line", "Body text for the dry run.", dry_run=True, outbox=Path(tmp))
        check("clean lead dry-runs successfully", res["status"] == "dry_run", res["status"])
        check("dry run reports a message id", bool(res["message_id"]), str(res["message_id"]))
        check("dry run writes nothing to a provider", res["external_http_calls"] == [], str(res["external_http_calls"]))
        check("provider attempts list stays empty on dry run", attempted_provider_calls == [], str(attempted_provider_calls))
        check("compliance verdict is allow", res["compliance"]["decision"] == "allow", str(res["compliance"]["reasons"]))
        check("outbox file written", bool(res["outbox_path"]) and Path(res["outbox_path"]).exists(), str(res["outbox_path"]))

        # 2. blocked lead
        res_b = send_outreach(BLOCKED_LEAD, "S", "B", dry_run=True, outbox=Path(tmp))
        check("unsubscribed lead is blocked", res_b["status"] == "blocked", res_b["status"])
        check("block reason names not_unsubscribed", res_b["reasons"] == ["not_unsubscribed"], str(res_b["reasons"]))
        check("blocked lead produced no message id", res_b["message_id"] is None)
        check("blocked lead made no external call", res_b["external_http_calls"] == [], str(res_b["external_http_calls"]))

        # 3. live mode without credentials
        res_l = send_outreach(CLEAN_LEAD, "S", "B", dry_run=False, outbox=Path(tmp))
        check("live send without credentials is refused", res_l["status"] == "provider_unavailable", res_l["status"])
        check("refused live send still made zero HTTP calls", external_http_calls == [], str(external_http_calls))
        check("refused live send is recorded as an attempt", len(attempted_provider_calls) == 1, str(attempted_provider_calls))

        # 4. parity with the gate module itself
        direct = compliance_gate.evaluate(CLEAN_LEAD)
        check("server verdict matches compliance_gate.evaluate", direct["decision"] == res["compliance"]["decision"],
              f"direct={direct['decision']} server={res['compliance']['decision']}")

    # 5. no SMTP anywhere, gate logic not duplicated here
    src = Path(__file__).read_text(encoding="utf-8")
    token = "smt" + "plib"  # assembled so this assertion cannot match itself
    check(f"no {token} import in the server", f"import {token}" not in src and f"{token}." not in src)
    check("server delegates to compliance_gate", "from orchestrator.policy import compliance_gate" in src)
    check("module-level external_http_calls is empty at exit", external_http_calls == [], str(external_http_calls))
    check("tool specs expose send_outreach + check_compliance",
          {t["name"] for t in tool_specs()} == {"send_outreach", "check_compliance", "describe_providers"})

    print(f"\nemail-outreach server selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="email-outreach MCP-style server (dry-run only in this session)")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--describe", action="store_true", help="print provider + tool configuration as JSON")
    p.add_argument("--check-lead", default="", help="path to a JSON lead file to gate-check")
    args = p.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return _selftest()
    if args.describe:
        print(json.dumps({"server": SERVER_NAME, "version": SERVER_VERSION,
                          "tools": tool_specs(), "providers": describe_providers()}, indent=2))
        return 0
    if args.check_lead:
        lead = json.loads(Path(args.check_lead).read_text(encoding="utf-8"))
        print(json.dumps(compliance_gate.evaluate(lead), indent=2))
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
