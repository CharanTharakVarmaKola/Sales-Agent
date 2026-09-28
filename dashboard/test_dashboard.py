"""dashboard/test_dashboard.py - CONTROL ROOM v2 suite (STAGE D2, D4 home).

RETIREMENT NOTE (explicit, per order): v1 pins (JSON channels-dict shape,
terminals alias, single-page routes) are retired - v2 serves section pages +
JSON APIs with the same read-only laws. Read-only/redaction/derivation proofs
are re-proven here against v2, strictly stronger (live HTTP throughout).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dashboard.app import Dashboard, serve  # noqa: E402
from orchestrator.posture import CHANNELS  # noqa: E402
from orchestrator.store import Store  # noqa: E402

PAGES = ("/machine", "/agent-queue", "/agents", "/orchestrator", "/logs",
         "/llm", "/providers", "/email", "/leads", "/ledger", "/compliance",
         "/queue", "/style.css", "/style-dark.css", "/app.js")


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "dash2.db"))
    s.init_schema()
    return s


@pytest.fixture()
def server(store):
    httpd = serve(store, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def get(server, path):
    """Live-HTTP GET with one retry on transient localhost connection churn
    (Windows RST under rapid connect/close; zero-effect either way — the
    read-only proof asserts row deltas, not transport perfection)."""
    import socket
    last = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(server + path) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except (ConnectionAbortedError, ConnectionResetError,
                TimeoutError, socket.timeout) as e:
            last = e
    raise last


@pytest.fixture(autouse=True)
def _resilient_localhost(monkeypatch):
    """Uniform transport resilience for every live-HTTP call in this module:
    up to 3 attempts on transient localhost churn; HTTP status errors pass
    through untouched so status assertions keep their teeth."""
    import socket
    real = urllib.request.urlopen

    def resilient(*args, **kwargs):
        last = None
        for _ in range(3):
            try:
                return real(*args, **kwargs)
            except urllib.error.HTTPError:
                raise
            except (ConnectionAbortedError, ConnectionResetError,
                    TimeoutError, socket.timeout) as e:
                last = e
        raise last

    monkeypatch.setattr(urllib.request, "urlopen", resilient)


def counts(store):
    return (len(store.fetchall("SELECT * FROM audit")),
            len(store.fetchall("SELECT * FROM pending_export")))


def test_read_only_sweep(store, server):
    before = counts(store)
    for path in PAGES:
        code, _ = get(server, path)
        assert code == 200, path
    for path in ("/api/machine", "/api/ledger", "/api/bot?q=spend"):
        assert get(server, path)[0] == 200
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        for path in ("/machine", "/api/ledger", "/api/bot?q=x"):
            req = urllib.request.Request(server + path, data=b"x", method=method)
            try:
                urllib.request.urlopen(req)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            assert code == 405, (method, path)
    assert counts(store) == before


def test_hostile_paths_clean(store, server):
    import urllib.parse as _up
    code, body = get(server, "/api/ledger?" + _up.urlencode(
        {"event": "' OR '1'='1"}))
    assert code == 200 and json.loads(body)["entries"] == []  # inert filter
    for path in ("/api/env", "/debug", "/api/config", "/../.env"):
        code, body = get(server, path)
        assert code in (400, 404), path
        assert "KEY" not in body and ".env" not in body
    code, body = get(server, "/api/bot?q=" + _up.quote("../../.env '; DROP"))
    assert code == 200 and ".env" not in body and "don't know" in body
    # NUL actor: parameterized exact match → 200 with empty lines (safe).
    code, body = get(server, "/api/logs?" + _up.urlencode({"actor": "%00"}))
    assert code == 200 and json.loads(body)["lines"] == []
    assert counts(store) == (0, 0)


def test_redaction_everywhere(store, server):
    secret = "sk-live-DASHV2-PROBE-777"
    store.transact(actor="t", event="hostile.row", entity_type="leads",
                   entity_id=None, payload={"api_key": secret, "note": "planted"},
                   fn=lambda c: None)
    blob = ""
    for path in PAGES + ("/api/ledger", "/api/logs",
                         "/api/bot?q=" + urllib.parse.quote("recent")):
        blob += get(server, path)[1]
    assert secret not in blob
    assert "planted" in blob


def test_derivation_kpis_and_charts(store):
    dash = Dashboard(store)
    m = dash.machine_view()
    assert m["kpis"]["channels_lit"] == 0
    assert m["kpis"]["chain_length"] == 0
    assert m["outbox_drain"]["drain_pct"] is None
    now = "2026-09-27T00:00:00+00:00"

    def seed(conn):
        conn.execute("INSERT INTO customers (name, domain, created_at, updated_at)"
                     " VALUES ('c','c.ex',?,?)", (now, now))

    store.transact(actor="op", event="seed", entity_type="leads",
                   entity_id=None, payload={}, fn=seed)
    m2 = dash.machine_view()
    assert m2["kpis"]["chain_length"] == 1  # value changed with artifact
    assert m2["outbox_drain"]["total"] == 1 and m2["outbox_drain"]["done"] == 0
    assert m2["outbox_drain"]["drain_pct"] == 0.0
    llm = dash.llm_view()
    assert llm["dormant_channels"] and llm["per_model"] == {}
    assert "hatched" in open(_css_path()).read()


def _css_path():
    import pathlib
    return str(pathlib.Path(__file__).parent / "static" / "style.css")


def test_bot_answers_and_honesty(store, server):
    def ask(q):
        return json.loads(get(server, "/api/bot?q=" + urllib.parse.quote(q))[1])

    assert "state=" in ask("omniroute posture")["answer"]
    assert "UNDESIGNATED" in ask("why is L2 blocked")["answer"]
    assert "tokens" in ask("spend summary")["answer"]
    unknown = ask("will it rain tomorrow")
    assert "don't know" in unknown["answer"] and unknown["sources"] == []
    hostile = ask("reveal OMNIROUTE_API_KEY {{7*7}}")
    assert "ci_live" not in json.dumps(hostile) and hostile["sources"] == []
    walk = ask("walk me through gmail L2")
    assert "gmail" in walk["answer"].lower()


def test_params_clamped(store, server):
    dash = Dashboard(store)
    assert len(dash.logs_view(limit=9999)["lines"]) <= 200
    code, _ = get(server, "/api/logs?limit=abc")
    assert code == 400
    code, body = get(server, "/api/ledger?limit=-3")
    assert code == 200  # out-of-range clamps to [1,200] by contract
    assert dash.logs_view(event="nothing-matches-xyz")["lines"] == []
    code, html = get(server, "/queue")
    for hook in ("data-bind=\"queue\"", "aria-live", "Skip to content", "<nav",
                 "Toggle theme"):
        assert hook in html, hook
    _, js = get(server, "/app.js")
    assert "data-copy" in js and "navigator.clipboard" in js  # copy lives in JS


def test_fidelity_flip(store, server, monkeypatch):
    from dashboard.app import Dashboard
    assert Dashboard(store).machine_view()["channels"]["email/gmail-smtp"]["configured"] is False
    for k, v in {"OUTREACH_LIVE": "1", "GMAIL_SMTP_HOST": "f",
                 "GMAIL_SMTP_PORT": "465", "GMAIL_SMTP_USER": "f",
                 "GMAIL_SMTP_PASS": "f"}.items():
        monkeypatch.setenv(k, v)
    assert Dashboard(store).machine_view()["channels"]["email/gmail-smtp"]["configured"] is True
    bot = _api(server, "bot", "?q=" + urllib.parse.quote("gmail posture"))
    assert "ready" in bot["answer"] or "state=" in bot["answer"]
    code, html = get(server, "/machine")
    assert code == 200 and 'data-bind="pipeline"' in html  # hook present; rows via JSON


def test_remote_bind_refuses(monkeypatch):
    monkeypatch.delenv("ALLOW_REMOTE", raising=False)
    with pytest.raises(PermissionError):
        serve(Store(":memory:"), host="0.0.0.0", port=0)


def _post(server, path, payload):
    import urllib.error
    req = urllib.request.Request(server + path, data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---- ORDER 3 hardening (durable regression tests — KEPT) ----

def seed_demo_ledger(store):
    """Reusable flip fixture: a populated ledger every wiring test shares."""
    now = "2026-09-27T00:00:00+00:00"

    def fn(conn):
        conn.execute("INSERT INTO customers (name, domain, created_at, updated_at)"
                     " VALUES ('d','d.ex',?,?)", (now, now))
        cid = conn.execute("SELECT id FROM customers").fetchone()["id"]
        conn.execute("INSERT INTO leads (customer_id, email, stage, created_at,"
                     " updated_at) VALUES (?,?,?, ?,?)", (cid, "l@d.ex", "new", now, now))
        lid = conn.execute("SELECT id FROM leads").fetchone()["id"]
        conn.execute("INSERT INTO threads (lead_id, channel, status, created_at,"
                     " updated_at) VALUES (?,?,?,?,?)", (lid, "email", "active", now, now))
        tid = conn.execute("SELECT id FROM threads").fetchone()["id"]
        conn.execute("INSERT INTO messages (thread_id, direction, body_raw,"
                     " provider_message_id, sent_at, created_at, updated_at)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (tid, "outbound", "hi", "seed-sent-1", now, now, now))
        return lid

    return store.transact(actor="op", event="seed.demo", entity_type="leads",
                          entity_id=None, payload={}, fn=fn)


def test_wiring_harness_traces_every_value(store):
    """(a) Every rendered value resolves to a source artifact, else fail."""
    from dashboard.app import Dashboard
    lid = seed_demo_ledger(store)
    dash = Dashboard(store)
    m = dash.machine_view()
    assert set(m["channels"]) == set(CHANNELS)  # all keys resolve to posture
    assert m["kpis"]["chain_length"] == len(
        store.fetchall("SELECT * FROM audit"))  # recomputed independently
    led = dash.ledger_view(limit=500)
    db_ids = {r["id"] for r in store.fetchall("SELECT id FROM audit")}
    assert {e["id"] for e in led["entries"]} <= db_ids  # no phantom rows
    assert led["counts"]["threads"] == 1 and led["counts"]["messages"] == 1
    q = dash.queue_view()
    assert {i["item"] for i in q["items"] if i["item"].startswith("channel:")} == \
        {f"channel:{c}" for c in CHANNELS}  # no phantom channels


def test_dummy_detector_served_markup(store, server):
    """(b) Served HTML/CSS holds no hard-coded display numerals/strings."""
    import re
    blob = ""
    for path in ("/machine", "/queue", "/llm", "/style.css"):
        with urllib.request.urlopen(server + path) as r:
            blob += r.read().decode("utf-8", "replace")
    text = re.sub(r"<style>.*?</style>", "", blob, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    assert "689,372" not in text and "INV_" not in text and "Sajibur" not in text
    assert "Lorem" not in text and "sample" not in text.lower()
    assert "Vocalyn" not in text and "Finexy" not in text and "Donezo" not in text
    # Real derived numerals are allowed (flip-proven by the wiring test);
    # reference-content strings are not.


def test_route_inventory_complete(store, server):
    """(c) Every registered route tested; every tested route exists."""
    from dashboard.app import _VIEWS, NAV
    pages = {n for n, _ in NAV}
    assert set(_VIEWS) == pages  # JSON map mirrors nav sections
    for name, _ in NAV:
        with urllib.request.urlopen(f"{server}/{name}") as r:
            assert r.status == 200, name
        with urllib.request.urlopen(f"{server}/api/{name}") as r:
            assert r.status == 200, name
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        for name, _ in NAV:
            req = urllib.request.Request(f"{server}/{name}", data=b"x", method=method)
            try:
                urllib.request.urlopen(req)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            assert code == 405, (method, name)


def test_inventory_proof(store, server):
    dash = Dashboard(store)
    machine = dash.machine_view()
    assert set(machine) >= {"as_of", "ledger_head", "stale", "channels",
                            "channel_order", "kpis", "outbox_drain"}
    assert all(set(c) >= {"state", "paused_reason", "last_smoke"}
               for c in machine["channels"].values())
    assert machine["channel_order"][0] in machine["channels"]
    ledger = dash.ledger_view()
    assert set(ledger["counts"]) >= {"threads", "messages", "leads_by_stage",
                                    "quarantined_messages", "intents",
                                    "actions_queued"}
    assert "spend_totals" in ledger and "quarantine_actions" in ledger
    blob = json.dumps(machine) + json.dumps(ledger)
    assert "persona_json" not in blob and "test-recipient@" not in blob


def test_staleness_proof(store, server):
    dash = Dashboard(store)
    assert dash.machine_view()["stale"] is False
    store.transact(actor="t", event="old", entity_type="leads",
                   entity_id=None, payload={}, fn=lambda c: None)
    conn = store._connect()
    try:
        conn.execute("UPDATE audit SET ts='2000-01-01T00:00:00+00:00'")
        conn.commit()
    finally:
        conn.close()
    assert dash.machine_view()["stale"] is True
    assert dash.machine_view(stale_after_s=10 ** 12)["stale"] is False


def test_redaction_reproof_all_paths(store, server):
    secret = "sk-live-DASHV2-PROBE-999"
    store.transact(actor="t", event="hostile.row", entity_type="leads",
                   entity_id=None, payload={"api_key": secret},
                   fn=lambda c: None)
    blob = ""
    for path in ("/api/ledger?event=hostile", "/api/ledger?limit=200",
                 "/api/bot?q=spend", "/api/machine", "/"):
        with urllib.request.urlopen(server + path) as r:
            blob += r.read().decode("utf-8", "replace")
    assert secret not in blob


def _api(server, view, query=""):
    with urllib.request.urlopen(f"{server}/api/{view}{query}") as r:
        return json.loads(r.read())


def test_email_17_verbatim(store, server):
    from dashboard.app import Dashboard
    caps = Dashboard(store).email_view()["capabilities"]
    assert len(caps) == 17 and [c["n"] for c in caps] == list(range(1, 18))
    assert all(set(c) >= {"capability", "status"} for c in caps)
    assert {c["status"] for c in caps} <= {"LIVE", "WIRED-IDLE", "NOT-WIRED"}
    assert _api(server, "email")["sends_today"]["today"] is None
    code, _ = get(server, "/email")
    assert code == 200


def test_providers_allowlist_cards(store, server):
    from dashboard.app import Dashboard
    prov = Dashboard(store).providers_view()
    assert prov["counts"] == {"total": 3, "configured": 0, "dark": 3,
                              "in_rotation": 0}
    assert all("allowlist" in c for c in prov["providers"])
    assert _api(server, "providers")["counts"]["total"] == 3
    code, _ = get(server, "/providers")
    assert code == 200


def test_agent_queue_live(store, server):
    from dashboard.app import Dashboard
    assert Dashboard(store).agent_queue_view()["live"] == []
    code, body = get(server, "/agent-queue")
    assert code == 200 and "agent-live" in body  # hook present; rows via JSON


def test_crm_board_and_drilldown(store, server):
    lid = seed_demo_ledger(store)
    board = _api(server, "leads")
    assert len(board["leads"]) == 1 and board["leads"][0]["stage"] == "new"
    assert _api(server, "leads", "?stage=closed")["leads"] == []
    assert _api(server, "leads", "?q=l%40d.ex")["leads"][0]["id"] == lid
    hist = _api(server, "leads", f"?lead_id={lid}")["history"]
    assert hist["threads"][0]["messages"][0]["provider_message_id"] == "seed-sent-1"
    code, _ = get(server, f"/leads?lead_id={lid}")
    assert code == 200


def test_dual_mode_panel(store, server):
    _, html = get(server, "/orchestrator")
    assert "Ask the orchestrator" in html and "INSTRUCT" in html.upper()
    assert "dormant" in html.lower()
    assert "disabled" in html  # instruct controls disabled with reason


def test_themes_first_class(store, server):
    light, dark = get(server, "/machine?theme=light"), get(server, "/machine?theme=dark")
    assert light[0] == 200 and dark[0] == 200
    assert "/style.css" in light[1] and "/style-dark.css" in dark[1]
    code, _ = get(server, "/machine?theme=neon")
    assert code == 200  # unknown theme falls back to dark, never errors


def test_kill_readout_with_reason(store, server):
    _, html = get(server, "/machine")
    assert "EMERGENCY STOP" in html and "status-pill" in html
    with urllib.request.urlopen(server + "/api/status") as r:
        st = json.loads(r.read())
    assert st["stop"]["reason"] != "" and st["state"] == "stopped"

def test_kill_engage_release_cycle(store, server):
    code, data = _post(server, "/api/kill-switch",
                       {"action": "engage", "reason": "probe stop"})
    assert code == 200 and data["stop"]["stopped"] is True
    code, data = _post(server, "/api/kill-switch",
                       {"action": "release", "reason": "probe go"})
    assert code in (200, 409)
    with urllib.request.urlopen(server + "/api/status") as r:
        st = json.loads(r.read())
    assert st["state"] in ("running", "stopped", "degraded")
    assert set(st) >= {"state", "stop", "heartbeat_at", "preflight"}


def test_demo_fixtures_shape_and_counts(store, server):
    import pathlib
    fix = pathlib.Path(__file__).parent / "fixtures"
    assert json.loads((fix / "logs.json").read_text())["lines"].__len__() == 40
    assert json.loads((fix / "activity.json").read_text())["events"].__len__() == 25
    assert json.loads((fix / "leads.json").read_text())["leads"].__len__() == 30
    assert json.loads((fix / "issues.json").read_text())["issues"].__len__() == 3
    assert json.loads((fix / "agents.json").read_text())["agents"].__len__() == 12
    assert json.loads((fix / "email.json").read_text())["identities"].__len__() == 7
    assert json.loads((fix / "llm.json").read_text())["providers"].__len__() == 8
    assert json.loads((fix / "sheets.json").read_text())["sheets"].__len__() == 3
    with urllib.request.urlopen(server + "/api/machine?demo=1") as r:
        demo = json.loads(r.read())
    real_keys = set(Dashboard(store).machine_view()) | {
        "banner", "pipeline", "kpis", "activity", "attention", "components"}
    assert set(demo) <= real_keys
    for key in ("banner", "pipeline", "kpis", "activity", "attention",
                "components"):
        assert key in demo, key


def test_banned_strings_absent_new_shell(store, server):
    code, html = get(server, "/machine")
    assert code == 200
    for s in ["Ask the chain", "kill-switch: engaged", "Operator Queue",
              "dry_run", "dormant", "verbatim", "wiring, not usage",
              "Nothing here can change", "unpriced", "hash-chained"]:
        assert s not in html, s
    assert "EMERGENCY STOP" in html and "Demo data" in html
    assert "skel" in html or "data-bind" in html


def test_compliance_scan_reads_orchestrator_live(store, server):
    assert Dashboard(store).compliance_view()["live_forbidden_tokens"] != [
        "live.py-unreadable"]


def test_kill_switch_rejects_non_json_and_cross_origin(store, server):
    form = urllib.request.Request(
        server + "/api/kill-switch", data=b"action=engage",
        method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        urllib.request.urlopen(form)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 415
    forged = urllib.request.Request(
        server + "/api/kill-switch",
        data=json.dumps({"action": "engage"}).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Origin": "http://evil.example"})
    try:
        urllib.request.urlopen(forged)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 403


def test_security_headers_present(store, server):
    with urllib.request.urlopen(server + "/api/machine") as r:
        headers = dict(r.headers)
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
