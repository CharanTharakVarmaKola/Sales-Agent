"""tests/test_prep.py — Batch B6-PREP tests (pytest, dry-run only).

Posture/kill-switch, channel status, live-adapter pause gating with mocked
env (monkeypatched, fake values only — never real creds, never fixtures),
consumer send/retry/dead-letter/audit flows with stubbed transports
(zero network), redaction pinning, _05 deprecation. No vendor imports.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.consumer import consume_once  # noqa: E402
from orchestrator.inference.base import InferencePaused  # noqa: E402
from orchestrator.live import (  # noqa: E402
    AgentMailSender, FreeLLMClient, GmailSender, LiveNotConfigured,
    OmniRouteClient, ZohoSender)
from orchestrator.posture import (  # noqa: E402
    CHANNELS, DEPRECATED_ENV, channel_status, is_live, posture_snapshot)
from orchestrator.redact import REDACTED, redact_for_audit  # noqa: E402
from orchestrator.store import Store  # noqa: E402

FAKE_ENV = {
    "OMNIROUTE_API_KEY": "fake-omniroute-value",
    "FREELLMAPI_API_KEY": "fake-freellmapi-value",
    "GMAIL_CLIENT_ID": "fake", "GMAIL_CLIENT_SECRET": "fake",
    "GMAIL_REFRESH_TOKEN": "fake",
    "AGENTMAIL_API_KEY_A": "fake-agentmail-value",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "fake-model",
    "GMAIL_SMTP_HOST": "fake", "GMAIL_SMTP_PORT": "465",
    "GMAIL_SMTP_USER": "fake", "GMAIL_SMTP_PASS": "fake",
    "AGENTMAIL_API_BASE_URL": "http://fake.local",
}
for i in (1, 2, 3, 4):
    for k in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "ACCOUNT_EMAIL"):
        FAKE_ENV[f"ZOHO_{k}_0{i}"] = "fake"
for i in (1, 2, 3, 4):
    for k in ("HOST", "PORT", "USER", "PASS"):
        FAKE_ENV[f"ZOHO_SMTP_0{i}_{k}"] = "fake" if k != "PORT" else "465"


@pytest.fixture()
def live_env(monkeypatch):
    monkeypatch.setenv("OUTREACH_LIVE", "1")
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "b6.db"))
    s.init_schema()
    s.set_stop(stopped=False, reason="test release", actor="test")
    return s


def seed_outbox(store, n=1):
    for i in range(n):
        store.transact(actor="t", event=f"seed.{i}", entity_type="send",
                       entity_id=i, payload={}, fn=lambda c: None)


SEND_KINDS = ("send",)


def call_consumer(store, **kw):
    from orchestrator.consumer import consume_once as _consume
    kw.setdefault("kinds", SEND_KINDS)
    return _consume(store, **kw)


def test_posture_defaults_dry():
    assert is_live() is False
    assert posture_snapshot() == {"live": False}
    assert OmniRouteClient().paused[0] is True


def test_unpaused_with_flag_and_fake_env(live_env):
    c = OmniRouteClient()
    assert c.paused == (False, "")
    c._transport = lambda messages: {"subject": "s", "body": "b", "claims": []}
    out = c.draft({"lead_id": "x"}, [])
    assert out["subject"] == "s" and c.last_route["primary"] == "omniroute"


def test_flag_without_env_stays_paused(monkeypatch):
    monkeypatch.setenv("OUTREACH_LIVE", "1")
    for k in FAKE_ENV:
        monkeypatch.delenv(k, raising=False)
    c = OmniRouteClient()
    assert c.paused[0] is True and "missing: OMNIROUTE_API_KEY" in c.paused[1]
    with pytest.raises(InferencePaused):
        c.draft({"x": 1}, [])


def test_transport_seam_raises_without_fake(live_env):
    with pytest.raises(LiveNotConfigured):
        OmniRouteClient().draft({"x": 1}, [])


def test_kill_switch_snapshot_semantics(monkeypatch, live_env):
    snap = posture_snapshot()
    assert snap == {"live": True}
    monkeypatch.setenv("OUTREACH_LIVE", "0")  # kill mid-flight
    assert snap == {"live": True}  # in-flight snapshot unchanged
    assert is_live() is False  # next run sees dry_run instantly
    assert OmniRouteClient().paused[0] is True


def test_channel_status_booleans_only(live_env):
    status = channel_status()
    assert set(status) == set(CHANNELS)
    blob = json.dumps(status)
    for v in FAKE_ENV.values():
        assert v not in blob
    assert all(set(v) == {"configured", "live_unlocked"} for v in status.values())
    assert all(v["configured"] for v in status.values())


def test_channel_unconfigured_without_env(monkeypatch):
    monkeypatch.delenv("OUTREACH_LIVE", raising=False)
    status = channel_status()
    assert status["email/gmail"] == {"configured": False, "live_unlocked": False}


def test_deprecated_05_never_read():
    import pathlib
    import re
    root = pathlib.Path(__file__).parent.parent / "orchestrator"
    readers = []
    for p in root.rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "_05" in line and "DEPRECATED_ENV" not in line and i > 1:
                # posture.py declares the deprecated names (allowed); any
                # environ READ of a _05 name is forbidden.
                if re.search(r"os\.environ|getenv|CHANNELS\s*\[", line):
                    readers.append(f"{p.name}:{i}")
    assert readers == [], f"_05 live reads: {readers}"
    assert any("_05" in v for v in DEPRECATED_ENV)  # deprecation documented


def test_bookkeeping_rows_never_sendable(store):
    seed_outbox(store, 1)

    class Strict:
        @staticmethod
        def send(draft, key):
            if draft.get("entity") == "pending_export":
                raise AssertionError("bookkeeping row sent")
            return {"ok": True}

    first = call_consumer(store, sender=Strict(), actor="t")
    assert first["sent"] == 1  # only the send row; bookkeeping untouched
    leftover = store.fetchall(
        "SELECT DISTINCT entity_type FROM pending_export WHERE done_ts IS NULL")
    assert {r["entity_type"] for r in leftover} == {"pending_export", "system"}
    second = call_consumer(store, sender=Strict(), actor="t")
    assert second["claimed"] == 0  # exhaust is never sendable


def test_consumer_send_done_audited(store):
    seed_outbox(store, 2)

    class FakeSender:
        def send(self, draft, key):
            return {"provider_id": "fake-1", "key": key}

    summary = call_consumer(store, sender=FakeSender(), actor="t")
    assert summary == {"claimed": 2, "sent": 2, "deferred": 0, "dead_lettered": 0}
    work_left = store.fetchall(
        "SELECT * FROM pending_export WHERE done_ts IS NULL"
        " AND entity_type='send'")
    assert work_left == []  # work drained; only audit exhaust remains
    events = {r["event"] for r in store.fetchall("SELECT event FROM audit")}
    assert "send.sent" in events


def test_consumer_transient_defers_then_recovers(store):
    seed_outbox(store, 1)
    calls = {"n": 0}

    class Flaky:
        def send(self, draft, key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient outage")
            return {"ok": True}

    s1 = call_consumer(store, sender=Flaky(), actor="t")
    assert s1["sent"] == 0 and s1["deferred"] == 1
    row = store.fetchone("SELECT * FROM pending_export")
    assert row["done_ts"] is None  # lease-held, reclaims later
    conn = store._connect()
    try:
        conn.execute("UPDATE pending_export SET claimed_ts='2000-01-01T00:00:00+00:00'")
        conn.commit()
    finally:
        conn.close()
    s2 = call_consumer(store, sender=Flaky(), actor="t")
    assert s2["sent"] == 1


def test_consumer_paused_sender_defers_silently(store):
    seed_outbox(store, 1)
    summary = call_consumer(store, sender=GmailSender(), actor="t")
    assert summary["deferred"] == 1 and summary["sent"] == 0
    assert store.fetchone("SELECT * FROM pending_export")["done_ts"] is None


def test_consumer_dead_letters_exhausted(store):
    seed_outbox(store, 1)
    conn = store._connect()
    try:
        conn.execute("UPDATE pending_export SET attempts=3, claimed_ts=NULL")
        conn.commit()
    finally:
        conn.close()

    class Never:
        def send(self, draft, key):
            raise AssertionError("must not attempt exhausted row")

    summary = call_consumer(store, sender=Never(), actor="t")
    assert summary["dead_lettered"] == 1
    row = store.fetchone("SELECT * FROM pending_export WHERE entity_type='send'")
    assert row["done_ts"] is not None
    events = [r["event"] for r in store.fetchall("SELECT event FROM audit")]
    assert "send.dead_lettered" in events


def test_redaction_pinned(store):
    secret = "sk-live-ATTEMPT-9f8e7d6c5b4a"
    payload = {"api_key": secret, "nested": {"bearer": "Bearer abc.def.ghi"},
               "note": "nothing sensitive here"}
    redacted = redact_for_audit(payload)
    assert secret not in json.dumps(redacted)
    assert REDACTED in json.dumps(redacted)
    assert redacted["note"] == "nothing sensitive here"
    store.transact(actor="t", event="redact.probe", entity_type="leads",
                   entity_id=None, payload=redacted, fn=lambda c: None)
    blob = "\n".join(r["payload_json"] for r in
                     store.fetchall("SELECT payload_json FROM audit"))
    assert secret not in blob and "Bearer abc.def.ghi" not in blob


def test_senders_pause_gated():
    assert GmailSender().paused[0] is True
    assert ZohoSender().paused[0] is True
    assert AgentMailSender().paused[0] is True
    assert FreeLLMClient().paused[0] is True


def test_ollama_missing_base_fails_closed(monkeypatch):
    from orchestrator.live import OllamaClient
    monkeypatch.setenv("OUTREACH_LIVE", "1")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    c = OllamaClient()
    assert c.paused[0] is True and "OLLAMA_BASE_URL" in c.paused[1]
    with pytest.raises(InferencePaused):
        c.draft({"lead_id": "x"}, [])


def test_ollama_unreachable_fails_closed(monkeypatch):
    import urllib.request as _url
    from urllib.error import URLError
    from orchestrator.live import OllamaClient
    monkeypatch.setenv("OUTREACH_LIVE", "1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    c = OllamaClient()
    assert c.paused == (False, "")

    def dead(req, timeout=None):
        raise URLError("refused")

    monkeypatch.setattr(_url, "urlopen", dead)
    with pytest.raises(ConnectionError):
        c.draft({"lead_id": "x"}, [])


def test_ollama_bad_model_fails_closed(monkeypatch):
    import io
    import urllib.request as _url
    from orchestrator.live import OllamaClient
    monkeypatch.setenv("OUTREACH_LIVE", "1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "no-such-model-xyz")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"error":"model not found"}'

    captured = {}

    def fake(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return FakeResp()

    monkeypatch.setattr(_url, "urlopen", fake)
    c = OllamaClient()
    with pytest.raises(ValueError):
        c.draft({"lead_id": "x"}, [])
    assert captured["auth"] is None  # keyless by design: no header, ever


def test_ollama_metering_lands(monkeypatch, live_env):
    import json as _json
    import urllib.request as _url
    from orchestrator.live import OllamaClient
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    body = _json.dumps({"model": "qwen3:8b",
                        "message": {"content": "Hi there"},
                        "prompt_eval_count": 20, "eval_count": 5}).encode()

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    monkeypatch.setattr(_url, "urlopen", lambda req, timeout=None: FakeResp())
    c = OllamaClient()
    out = c.draft({"lead_id": "x"}, [])
    assert out["body"] == "Hi there"
    assert c.last_usage == {"model": "qwen3:8b", "prompt_tokens": 20,
                            "completion_tokens": 5, "total_tokens": 25,
                            "cost_estimate": 0.0,
                            "cost_basis": "local-inference"}


def test_omniroute_transport_parses_and_meters(monkeypatch, live_env):
    import io
    import json as _json
    import urllib.request as _url

    monkeypatch.setenv("OMNIROUTE_MODEL", "fake-model")
    body = _json.dumps({
        "model": "fake-model",
        "choices": [{"message": {"content": "Hello prospect"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3,
                  "total_tokens": 15},
    }).encode("utf-8")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    monkeypatch.setattr(_url, "urlopen", lambda req, timeout=None: FakeResp())
    c = OmniRouteClient()
    out = c.draft({"lead_id": "x"}, [])
    assert out == {"subject": "(untitled)", "body": "Hello prospect", "claims": []}
    assert c.last_usage["total_tokens"] == 15
    assert c.last_usage["cost_estimate"] is None

    from orchestrator.nodes import draft_node
    ctx = {"inference": c, "now": "2026-09-27T00:00:00+00:00", "trail": []}
    c2 = OmniRouteClient()
    c2._transport = lambda messages: {"subject": "s", "body": "b", "claims": []}
    c2.last_usage = {"model": "fake-model", "total_tokens": 4,
                     "cost_estimate": None, "cost_basis": "unpriced"}
    draft_node({"lead": {"lead_id": "x"}}, {"inference": c2, "now": ctx["now"],
                                            "trail": ctx["trail"]})
    metered = [e for e in ctx["trail"] if e.get("outcome") == "metered"]
    assert len(metered) == 1 and metered[0]["usage"]["total_tokens"] == 4


def test_static_scans_prep():
    import pathlib
    root = pathlib.Path(__file__).parent.parent / "orchestrator"
    for name in ("posture.py", "redact.py", "consumer.py"):
        low = (root / name).read_text(encoding="utf-8").lower()
        for token in ("socket", "requests", "http.client", "urlopen",
                      "sqlite3", "import datetime"):
            assert token not in low, f"{name}:{token}"
    # B6-LIVE + STAGE P1: live.py alone may use stdlib transports for the
    # authorized channels — urllib (inference/API) and the stdlib mail stack
    # (smtplib/imaplib/email/socket). Nothing else network-shaped may appear.
    live_src = (root / "live.py").read_text(encoding="utf-8")
    low = live_src.lower()
    assert "urllib.request" in low  # the one sanctioned HTTP import
    assert "smtplib" in low and "imaplib" in low  # the sanctioned mail stack
    for token in ("requests", "http.client", "aiohttp", "httpx",
                  "sqlite3", "import datetime", "api_key="):
        assert token not in low, f"live.py:{token}"
