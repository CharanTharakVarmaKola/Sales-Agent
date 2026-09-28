"""scripts/live_smoke_omniroute.py — B6-LIVE first-live-send smoke (llm/omniroute).

One minimal real call, env-only creds, spend row audited end-to-end.
Fail-closed: refuses unless OUTREACH_LIVE=1, OMNIROUTE_API_KEY and
OMNIROUTE_MODEL are all present. Prints model/tokens/audit id — NEVER the key.
Run explicitly by the operator/swarm for the live event; NOT part of pytest.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.live import OmniRouteClient
from orchestrator.redact import redact_for_audit
from orchestrator.store import Store


def fail(msg: str) -> int:
    print(f"REFUSED: {msg}")
    return 2


def main() -> int:
    db_path = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv \
        else os.path.join(os.path.dirname(__file__), "live-smoke.db")
    if os.environ.get("OUTREACH_LIVE") != "1":
        return fail("OUTREACH_LIVE != 1 (per-channel unlock requires the flag)")
    if not os.environ.get("OMNIROUTE_API_KEY"):
        return fail("OMNIROUTE_API_KEY absent")
    if not os.environ.get("OMNIROUTE_MODEL"):
        return fail("OMNIROUTE_MODEL unset — fail closed, no guessed model")

    client = OmniRouteClient()
    print(f"paused={client.paused}")
    if client.paused[0]:
        return fail(f"client paused: {client.paused[1]}")

    lead = {"lead_id": "smoke-omniroute", "company": "smoke-co",
            "name": "Smoke Test", "email": "smoke@example.invalid"}
    try:
        out = client.draft(lead, [])
    except Exception as exc:
        print(f"CALL FAILED: {type(exc).__name__}")
        return 1
    print(f"draft keys={sorted(out)} body_chars={len(out.get('body', ''))}")

    store = Store(db_path)
    store.init_schema()
    payload = redact_for_audit({
        "run_id": "smoke-omniroute-1",
        "channel": "llm/omniroute",
        "last_route": client.last_route,
        "usage": client.last_usage,
    })

    def fn(conn):
        return None

    store.transact(actor="operator", event="live.smoke.omniroute",
                   entity_type="channel", entity_id=None,
                   payload=payload, fn=fn)
    row = store.fetchone(
        "SELECT id FROM audit WHERE event='live.smoke.omniroute'"
        " ORDER BY id DESC LIMIT 1")
    ok, msg = store.verify_chain()
    print(f"audit_id={row['id']} chain_ok={ok} ({msg})")
    usage = client.last_usage or {}
    print(f"model={usage.get('model')} "
          f"prompt={usage.get('prompt_tokens')} "
          f"completion={usage.get('completion_tokens')} "
          f"total={usage.get('total_tokens')} cost={usage.get('cost_estimate')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
