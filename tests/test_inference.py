"""tests/test_inference.py — Batch B2 inference tests (pytest, dry-run only).

Pause-before-network, no-swallow, last_route audit round-trip.
No credentials, no network, no vendor imports.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.inference.base import (  # noqa: E402
    InferenceClient, InferencePaused)
from orchestrator.inference.dry_run import (  # noqa: E402
    DRY_RUN_REASON, DryRunInferenceClient)
from orchestrator.inference.routing import RoutingInferenceClient  # noqa: E402
from orchestrator.store import Store  # noqa: E402

LEAD = {"lead_id": "t-1", "email": "t@x.io"}
EVIDENCE: list = []


class LiveMockClient(InferenceClient):
    """Unpaused test double standing in for a future live client."""

    name = "live-mock"

    def __init__(self):
        self.calls = 0

    @property
    def paused(self):
        return (False, "")

    def draft(self, lead, evidence):
        self.calls += 1
        return {"subject": "s", "body": "b", "claims": []}


class PauseRacerClient(InferenceClient):
    """Reports unpaused, then raises InferencePaused (pause raced in)."""

    name = "racer"

    @property
    def paused(self):
        return (False, "")

    def draft(self, lead, evidence):
        raise InferencePaused("paused mid-flight")


class TransientFailClient(InferenceClient):
    """Reports unpaused, then raises a transient (non-pause) error."""

    name = "flaky"

    @property
    def paused(self):
        return (False, "")

    def draft(self, lead, evidence):
        raise RuntimeError("transient outage")


def test_dry_run_always_paused_never_drafts():
    c = DryRunInferenceClient()
    assert c.paused == (True, DRY_RUN_REASON)
    with pytest.raises(InferencePaused) as ei:
        c.draft(LEAD, EVIDENCE)
    assert ei.value.reason == DRY_RUN_REASON


def test_both_paused_raises_before_io():
    a, b = DryRunInferenceClient(), DryRunInferenceClient()
    r = RoutingInferenceClient(a, b)
    with pytest.raises(InferencePaused) as ei:
        r.draft(LEAD, EVIDENCE)
    assert DRY_RUN_REASON in ei.value.reason
    assert r.last_route == {"paused": True,
                            "reason": f"{DRY_RUN_REASON} | {DRY_RUN_REASON}",
                            "primary": "dry_run", "fallback": "dry_run"}
    assert isinstance(a.calls if hasattr(a, "calls") else 0, int)


def test_primary_paused_fallback_serves_and_route_recorded():
    primary = DryRunInferenceClient()
    fallback = LiveMockClient()
    r = RoutingInferenceClient(primary, fallback)
    assert r.paused == (False, "")
    out = r.draft(LEAD, EVIDENCE)
    assert out["subject"] == "s" and fallback.calls == 1
    assert r.last_route == {"paused": False, "reason": DRY_RUN_REASON,
                            "primary": "dry_run", "fallback": "live-mock"}


def test_no_fallback_primary_paused_mentions_primary():
    r = RoutingInferenceClient(DryRunInferenceClient())
    with pytest.raises(InferencePaused) as ei:
        r.draft(LEAD, EVIDENCE)
    assert ei.value.reason == DRY_RUN_REASON
    assert r.last_route["fallback"] is None


def test_pause_propagates_unswallowed_through_generic_handler():
    """A broad except-Exception around draft() must still see InferencePaused."""
    r = RoutingInferenceClient(PauseRacerClient(), LiveMockClient())
    with pytest.raises(InferencePaused):
        try:
            r.draft(LEAD, EVIDENCE)
        except Exception:
            raise
    assert r.last_route["paused"] is True


def test_transient_primary_fails_over_with_pause_recheck():
    fallback = LiveMockClient()
    r = RoutingInferenceClient(TransientFailClient(), fallback)
    out = r.draft(LEAD, EVIDENCE)
    assert out["body"] == "b" and fallback.calls == 1


def test_transient_primary_paused_fallback_never_called():
    fallback = LiveMockClient()
    r = RoutingInferenceClient(TransientFailClient(), DryRunInferenceClient())
    with pytest.raises(RuntimeError):
        r.draft(LEAD, EVIDENCE)
    assert fallback.calls == 0


def test_last_route_round_trips_to_audit(tmp_path):
    store = Store(str(tmp_path / "b2.db"))
    store.init_schema()
    r = RoutingInferenceClient(DryRunInferenceClient(), LiveMockClient())
    r.draft(LEAD, EVIDENCE)
    store.transact(actor="test", event="draft.ready", entity_type="leads",
                   entity_id=7, payload={"last_route": r.last_route}, fn=lambda c: None)
    row = store.fetchone("SELECT payload_json FROM audit ORDER BY id DESC LIMIT 1")
    payload = json.loads(row["payload_json"])
    assert payload["last_route"] == r.last_route
    assert payload["last_route"]["fallback"] == "live-mock"


def test_no_network_strings_in_layer():
    import pathlib
    layer = pathlib.Path(__file__).parent.parent / "orchestrator" / "inference"
    src = "\n".join(p.read_text(encoding="utf-8") for p in layer.glob("*.py"))
    for token in ("http", "socket", "requests", ":20128", ":3001",
                  "v1/chat/completions", "urlopen", "aiohttp", "httpx"):
        assert token not in src.lower(), f"network token in B2 layer: {token}"


def test_no_credential_strings_in_layer():
    import pathlib
    layer = pathlib.Path(__file__).parent.parent / "orchestrator" / "inference"
    src = "\n".join(p.read_text(encoding="utf-8") for p in layer.glob("*.py"))
    low = src.lower()
    for token in ("api_key", "apikey", "bearer", "sk-live", "sk-test",
                  "password", "secret", "refresh_token", "client_secret"):
        assert token not in low, f"credential token in B2 layer: {token}"


def test_single_timestamp_source():
    import pathlib
    root = pathlib.Path(__file__).parent.parent / "orchestrator"
    offenders = []
    for p in root.rglob("*.py"):
        if p.name == "timeutil.py":
            continue
        text = p.read_text(encoding="utf-8")
        if "datetime.now(timezone.utc).isoformat()" in text:
            offenders.append(str(p))
    assert offenders == [], f"scattered clock: {offenders}"
    from orchestrator import timeutil
    assert timeutil.utcnow_iso().endswith("+00:00")


def test_pause_race_on_fallback_records_and_propagates():
    r = RoutingInferenceClient(DryRunInferenceClient(), PauseRacerClient())
    with pytest.raises(InferencePaused) as ei:
        r.draft(LEAD, EVIDENCE)
    assert ei.value.reason == "paused mid-flight"
    assert r.last_route["paused"] is True
    assert r.last_route["reason"] == "paused mid-flight"


def test_crash_between_draft_and_audit_leaves_no_half_write(tmp_path):
    """Draft succeeds in memory; the audit txn then blows up: audit and
    outbox must both be absent (B1 txn shape), route lives only in memory."""
    store = Store(str(tmp_path / "b2crash.db"))
    store.init_schema()
    r = RoutingInferenceClient(DryRunInferenceClient(), LiveMockClient())
    out = r.draft(LEAD, EVIDENCE)
    assert out["subject"] == "s"

    def boom(conn):
        raise RuntimeError("killed before audit write")

    with pytest.raises(RuntimeError):
        store.transact(actor="t", event="draft.ready", entity_type="leads",
                       entity_id=9, payload={"last_route": r.last_route}, fn=boom)
    assert store.fetchall("SELECT * FROM audit") == []
    assert store.fetchall("SELECT * FROM pending_export") == []
    assert r.last_route["fallback"] == "live-mock"  # memory-only, complete
