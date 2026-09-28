"""tests/test_transport.py — STAGE P1 outreach transports (pytest, dry only).

In-process fake SMTP/IMAP servers (monkeypatched stdlib classes — no
sockets, no creds, no network). Missing/unreachable/lease/retry/dead-letter
+ L1 credential-shape vocabulary. Fake values only.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestrator.consumer import consume_once  # noqa: E402
from orchestrator.inference.base import InferencePaused  # noqa: E402
from orchestrator.live import (  # noqa: E402
    AgentMailApiSender, GmailSmtpSender, SendUnknown, SmtpHardError,
    ZohoSmtpSender, lease_window_s, resolve_unknown)
from orchestrator.posture import CHANNELS  # noqa: E402
from orchestrator.store import Store  # noqa: E402

SMTP_ENV = {"GMAIL_SMTP_HOST": "fake", "GMAIL_SMTP_PORT": "465",
            "GMAIL_SMTP_USER": "fake", "GMAIL_SMTP_PASS": "fake",
            "OUTREACH_LIVE": "1"}


class FakeSMTP:
    """In-process fake SMTP_SSL: records, injects latency/failure modes."""
    mode = "ok"
    delivered: list = []
    calls = 0

    def __init__(self, host, port, timeout=None):
        if FakeSMTP.mode == "unreachable":
            raise OSError("fake connect refused")
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, secret):
        if FakeSMTP.mode == "auth-fail":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"fake denied")

    def send_message(self, msg):
        FakeSMTP.calls += 1
        if FakeSMTP.mode == "timeout-unknown":
            import socket
            raise socket.timeout("fake stall post-DATA")
        if FakeSMTP.mode == "flaky-once" and FakeSMTP.calls == 1:
            raise OSError("fake transient")
        FakeSMTP.delivered.append(msg)
        return {}


class FakeIMAP:
    def __init__(self, host, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, secret):
        return ("OK", [])

    def select(self, box, readonly=True):
        return ("OK", [])

    def search(self, charset, *criteria):
        return ("OK", [b"1 2"])

    def fetch(self, uid, spec):
        return ("OK", [(b"1", b"From: a@x.io\r\nSubject: hi\r\n")])

    def logout(self):
        return ("OK", [])


@pytest.fixture()
def smtp_fake(monkeypatch):
    import smtplib
    import imaplib
    FakeSMTP.mode = "ok"
    FakeSMTP.delivered = []
    FakeSMTP.calls = 0
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    for k, v in SMTP_ENV.items():
        monkeypatch.setenv(k, v)
    return FakeSMTP


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "p1.db"))
    s.init_schema()
    s.set_stop(stopped=False, reason="test release", actor="test")
    return s


def test_missing_creds_closed(monkeypatch):
    monkeypatch.setenv("OUTREACH_LIVE", "1")
    for k in SMTP_ENV:
        monkeypatch.delenv(k, raising=False)
    s = GmailSmtpSender()
    assert s.paused[0] is True
    with pytest.raises(InferencePaused):
        s.send({"to": "a@x.io"}, "k1")


def test_unreachable_closed(smtp_fake):
    from orchestrator.live import SmtpTransient
    FakeSMTP.mode = "unreachable"
    with pytest.raises(SmtpTransient):  # known-negative: nothing was sent
        GmailSmtpSender().send({"to": "a@x.io", "subject": "s", "body": "b"}, "k2")


def test_lease_expiry_resolves_through_dedupe(store, smtp_fake):
    FakeSMTP.mode = "timeout-unknown"
    sender = GmailSmtpSender()
    key = "p1-lease-key"
    with pytest.raises(SendUnknown) as ei:
        sender.send({"to": "a@x.io", "subject": "s", "body": "b"}, key)
    assert ei.value.idempotency_key == key
    assert resolve_unknown(store, key) is False  # effect not yet recorded
    # Late-arriving effect lands (provider did send): record the key.
    now = "2026-09-27T00:00:00+00:00"

    def fn(conn):
        conn.execute(
            "INSERT INTO actions (lead_id, action_type, status, idempotency_key,"
            " created_at, updated_at) VALUES (1,'send_draft','sent',?,?,?)",
            (key, now, now))

    # seed a lead row for FK validity
    def seed(conn):
        conn.execute("INSERT INTO customers (name, domain, created_at, updated_at)"
                     " VALUES ('c','c.ex',?,?)", (now, now))
        cid = conn.execute("SELECT id FROM customers").fetchone()["id"]
        conn.execute("INSERT INTO leads (customer_id, email, created_at, updated_at)"
                     " VALUES (?,?,?,?)", (cid, "l@c.ex", now, now))

    store.transact(actor="t", event="seed", entity_type="leads",
                   entity_id=None, payload={}, fn=seed)
    store.transact(actor="t", event="effect", entity_type="actions",
                   entity_id=None, payload={}, fn=fn)
    assert resolve_unknown(store, key) is True  # suppress resend
    assert FakeSMTP.calls == 1  # exactly one wire attempt, no duplicate


def test_retry_honest_second_attempt(store, smtp_fake):
    from orchestrator.live import SendUnknown
    FakeSMTP.mode = "flaky-once"
    sender = GmailSmtpSender()
    # Attempt 1: mid-send stall → unknown outcome, no blind retry inside send().
    with pytest.raises(SendUnknown):
        sender.send({"to": "a@x.io", "subject": "s", "body": "b"}, "k3")
    assert resolve_unknown(store, "k3") is False  # no effect recorded yet
    # Caller reconciles, finds nothing, retries honestly: attempt 2 lands.
    out = sender.send({"to": "a@x.io", "subject": "s", "body": "b"}, "k3")
    assert FakeSMTP.calls == 2 and out["idempotency_key"] == "k3"


def test_hard_failure_dead_letters(store, smtp_fake):
    FakeSMTP.mode = "auth-fail"
    with pytest.raises(SmtpHardError):
        GmailSmtpSender().send({"to": "a@x.io", "subject": "s", "body": "b"}, "k4")
    # Consumer path: exhausted attempts → dead_lettered, never sent.
    store.transact(actor="t", event="seed", entity_type="send",
                   entity_id=9, payload={}, fn=lambda c: None)
    conn = store._connect()
    try:
        conn.execute("UPDATE pending_export SET attempts=3, claimed_ts=NULL")
        conn.commit()
    finally:
        conn.close()

    class Never:
        def send(self, draft, key):
            raise AssertionError("exhausted row attempted")

    summary = consume_once(store, sender=Never(), actor="t", kinds=("send",))
    assert summary["dead_lettered"] == 1
    events = [r["event"] for r in store.fetchall("SELECT event FROM audit")]
    assert "send.dead_lettered" in events


def test_imap_poll_shape(smtp_fake):
    rows = GmailSmtpSender().poll_inbox()
    assert len(rows) == 2 and all("raw" in r for r in rows)


def test_zoho_slots_isolated(monkeypatch, smtp_fake):
    monkeypatch.setenv("ZOHO_SMTP_02_HOST", "fake")
    monkeypatch.setenv("ZOHO_SMTP_02_PORT", "465")
    monkeypatch.setenv("ZOHO_SMTP_02_USER", "fake")
    monkeypatch.setenv("ZOHO_SMTP_02_PASS", "fake")
    ZohoSmtpSender(slot=2).send({"to": "a@x.io", "subject": "s", "body": "b"}, "kz")
    assert len(FakeSMTP.delivered) == 1
    with pytest.raises(ValueError):
        ZohoSmtpSender(slot=5)


def test_agentmail_shapes(monkeypatch):
    import json as _json
    import urllib.request as _url
    monkeypatch.setenv("OUTREACH_LIVE", "1")
    monkeypatch.delenv("AGENTMAIL_API_BASE_URL", raising=False)
    monkeypatch.setenv("AGENTMAIL_API_KEY_A", "fake")
    with pytest.raises(InferencePaused):
        AgentMailApiSender().send({"to": "a@x.io"}, "ka")  # base missing → paused
    monkeypatch.setenv("AGENTMAIL_API_BASE_URL", "http://fake.local")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"id": "fake-msg-1"}).encode()

    seen = {}
    real = AgentMailApiSender._transport

    def fake(self, draft, key):
        seen["key"] = key
        return real(self, draft, key)

    monkeypatch.setattr(_url, "urlopen", lambda req, timeout=None: FakeResp())
    out = AgentMailApiSender().send({"to": "a@x.io", "subject": "s", "body": "b"}, "kb")
    assert out == {"provider_id": "fake-msg-1", "idempotency_key": "kb"}


def test_l1_credential_vocabulary():
    assert CHANNELS["email/gmail-smtp"] == ("GMAIL_SMTP_HOST", "GMAIL_SMTP_PORT",
                                           "GMAIL_SMTP_USER", "GMAIL_SMTP_PASS")
    zoho = CHANNELS["email/zoho-smtp"]
    assert len(zoho) == 16 and all("_05" not in n for n in zoho)
    assert CHANNELS["email/agentmail-api"] == ("AGENTMAIL_API_BASE_URL",
                                              "AGENTMAIL_API_KEY_A")
    assert lease_window_s() == 600


def test_no_secret_flow_into_metering(smtp_fake):
    sender = GmailSmtpSender()
    out = sender.send({"to": "a@x.io", "subject": "s", "body": "b"}, "k5")
    assert set(sender.last_usage) <= {"channel", "messages", "bytes",
                                      "cost_estimate", "cost_basis"}
    assert set(out) == {"refused", "idempotency_key"}
    import json as _json
    blob = _json.dumps([out, sender.last_usage])
    assert "GMAIL_SMTP_PASS" not in blob and "Authorization" not in blob
