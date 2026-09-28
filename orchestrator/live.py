"""orchestrator/live.py — live channel adapters behind the posture flag (B6-PREP).

Implements docs/parity-B6.md §§3,6. Adapters read env ONLY at call time;
no value is stored, cached, logged, or committed. paused is True unless
BOTH the kill-switch flag is set AND the channel's env is present — so no
network path is reachable without creds+flag. _transport() is THE seam:
it raises LiveNotConfigured in PREP (no live code paths exist yet); tests
replace it with fakes (mocked credentials via monkeypatched env only —
never real values, never fixtures). B2 raising boundary + catch-by-name
apply unchanged. Real transports land in B6-LIVE per channel after smoke.
"""
from __future__ import annotations

import json as _json
import os
import urllib.request as _url

from orchestrator.inference.base import InferenceClient, InferencePaused
from orchestrator.posture import CHANNELS, is_live

OMNIROUTE_DEFAULT_BASE = "https://api.cheaperinference.com/v1"
TRANSPORT_TIMEOUT_S = 60


class LiveNotConfigured(Exception):
    """Transport invoked without an activated channel. Never swallowed."""


def _present(names: tuple[str, ...]) -> bool:
    return all(os.environ.get(n) for n in names)


def _missing(names: tuple[str, ...]) -> list[str]:
    return [n for n in names if not os.environ.get(n)]


class LiveChannelClient(InferenceClient):
    """Base: pause-first live LLM channel. Subclass sets channel + transport."""

    channel = ""
    system_prompt = "You are a precise sales assistant. Reply with JSON only."

    def __init__(self):
        # last_usage: metering side-channel for the audit chain (B6-LIVE).
        # Set by _transport on success; read by callers for spend rows.
        # Never holds credentials — model/tokens/cost-basis only.
        self.last_usage: dict | None = None
        self.last_route: dict | None = None

    @property
    def _env_names(self) -> tuple[str, ...]:
        return CHANNELS[self.channel]

    @property
    def paused(self) -> tuple[bool, str]:
        if not is_live():
            return (True, "dry_run: kill-switch engaged")
        missing = _missing(self._env_names)
        if missing:
            return (True, f"dry_run: {self.channel} missing: {','.join(missing)}")
        return (False, "")

    def _transport(self, messages: list[dict]) -> dict:
        """THE seam. PREP raises; B6-LIVE implements per channel; tests fake it."""
        raise LiveNotConfigured(f"{self.channel}: transport lands in B6-LIVE")

    def draft(self, lead: dict, evidence: list[dict]) -> dict:
        p_paused, reason = self.paused
        if p_paused:
            self.last_route = {"paused": True, "reason": reason,
                               "primary": self.name, "fallback": None}
            raise InferencePaused(reason)
        messages = [{"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": (
                        f"lead={lead} evidence={evidence}")}]
        try:
            out = self._transport(messages)
        except InferencePaused:
            raise
        self.last_route = {"paused": False, "reason": "",
                           "primary": self.name, "fallback": None}
        if not isinstance(out, dict) or not {"subject", "body", "claims"} <= set(out):
            raise ValueError(f"{self.name}: transport broke the draft contract")
        return out


class OmniRouteClient(LiveChannelClient):
    channel = "llm/omniroute"
    name = "omniroute"

    def _transport(self, messages: list[dict]) -> dict:
        """B6-LIVE: OpenAI-compatible POST. First network code in this repo.

        Reads OMNIROUTE_BASE_URL (default: cheaperinference), OMNIROUTE_MODEL
        (REQUIRED — fail closed, no guessed model), OMNIROUTE_API_KEY at call
        time. The key travels only in the Authorization header; it never
        enters return values, attributes (other than transient locals), logs,
        or audit payloads. Contract violations raise; transports raise
        ConnectionError (transient) or ValueError (shape) — never silent.
        """
        base = os.environ.get("OMNIROUTE_BASE_URL",
                              OMNIROUTE_DEFAULT_BASE).rstrip("/")
        model = os.environ.get("OMNIROUTE_MODEL", "")
        if not model:
            raise LiveNotConfigured("omniroute: OMNIROUTE_MODEL unset — fail closed")
        key = os.environ.get("OMNIROUTE_API_KEY", "")
        if not key:
            raise LiveNotConfigured("omniroute: key absent at call time")
        body = _json.dumps({"model": model, "messages": messages}).encode("utf-8")
        req = _url.Request(
            base + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key},
            method="POST")
        try:
            with _url.urlopen(req, timeout=TRANSPORT_TIMEOUT_S) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except LiveNotConfigured:
            raise
        except Exception as exc:
            raise ConnectionError(
                f"omniroute transport failed: {type(exc).__name__}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {}) or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("omniroute: unexpected response shape") from exc
        self.last_usage = {
            "model": payload.get("model", model),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost_estimate": None,
            "cost_basis": "unpriced: no rate card configured",
        }
        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith("{"):
                try:
                    data = _json.loads(stripped)
                except ValueError:
                    data = None
                if isinstance(data, dict) and {"subject", "body", "claims"} <= set(data):
                    return data
            # Honest wrap: raw body, zero claims (verifier treats as ungrounded).
            return {"subject": "(untitled)", "body": content, "claims": []}
        raise ValueError("omniroute: non-string message content")


class FreeLLMClient(LiveChannelClient):
    channel = "llm/freellmapi"
    name = "freellmapi"


class OllamaClient(LiveChannelClient):
    channel = "llm/ollama"
    name = "ollama"

    def _transport(self, messages: list[dict]) -> dict:
        """B6-LIVE-ollama: native Ollama /api/chat. Keyless BY DESIGN, not by
        omission: no Authorization header is constructed anywhere on this
        path (explicit no-auth; a dummy key would be a lie the secrets-audit
        could not distinguish from a leak). OLLAMA_BASE_URL required (fail
        closed); OLLAMA_MODEL required (fail closed, no guessed model).
        Local inference: cost_estimate 0.0, cost_basis local-inference.
        """
        base = os.environ.get("OLLAMA_BASE_URL", "").rstrip("/")
        if not base:
            raise LiveNotConfigured("ollama: OLLAMA_BASE_URL unset — fail closed")
        model = os.environ.get("OLLAMA_MODEL", "")
        if not model:
            raise LiveNotConfigured("ollama: OLLAMA_MODEL unset — fail closed")
        adapted = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                   for m in messages]
        body = _json.dumps({"model": model, "messages": adapted,
                            "stream": False}).encode("utf-8")
        req = _url.Request(
            base + "/api/chat", data=body,
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with _url.urlopen(req, timeout=TRANSPORT_TIMEOUT_S * 5) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except LiveNotConfigured:
            raise
        except Exception as exc:
            raise ConnectionError(
                f"ollama transport failed: {type(exc).__name__}") from exc
        try:
            content = payload["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ValueError("ollama: unexpected response shape") from exc
        prompt_n = payload.get("prompt_eval_count")
        completion_n = payload.get("eval_count")
        self.last_usage = {
            "model": payload.get("model", model),
            "prompt_tokens": prompt_n,
            "completion_tokens": completion_n,
            "total_tokens": ((prompt_n or 0) + (completion_n or 0)
                             if prompt_n is not None or completion_n is not None
                             else None),
            "cost_estimate": 0.0,
            "cost_basis": "local-inference",
        }
        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith("{"):
                try:
                    data = _json.loads(stripped)
                except ValueError:
                    data = None
                if isinstance(data, dict) and {"subject", "body", "claims"} <= set(data):
                    return data
            return {"subject": "(untitled)", "body": content, "claims": []}
        raise ValueError("ollama: non-string message content")


class LiveEmailSender:
    """Pause-first email sender. send() maps the Rule 7 key onto the request."""

    channel = ""
    name = "email-sender"

    def __init__(self):
        self.last_usage = None
        self.last_route = None

    @property
    def _env_names(self) -> tuple[str, ...]:
        return CHANNELS[self.channel]

    @property
    def paused(self) -> tuple[bool, str]:
        if not is_live():
            return (True, "dry_run: kill-switch engaged")
        missing = _missing(self._env_names)
        if missing:
            return (True, f"dry_run: {self.channel} missing: {','.join(missing)}")
        return (False, "")

    def _transport(self, draft: dict, idempotency_key: str) -> dict:
        raise LiveNotConfigured(f"{self.channel}: transport lands in B6-LIVE")
    def send(self, draft: dict, idempotency_key: str) -> dict:
        p_paused, reason = self.paused
        if p_paused:
            raise InferencePaused(reason)
        try:
            receipt = self._transport(draft, idempotency_key)
        except InferencePaused:
            raise
        if not isinstance(receipt, dict):
            raise ValueError(f"{self.name}: transport broke the send contract")
        return receipt


class GmailSender(LiveEmailSender):
    channel = "email/gmail"
    name = "gmail"


class ZohoSender(LiveEmailSender):
    channel = "email/zoho"
    name = "zoho"


class AgentMailSender(LiveEmailSender):
    channel = "email/agentmail"
    name = "agentmail"


# ── STAGE P1 transports (parity-B6 §10) ──────────────────────────────

LEASE_ENV = "OUTREACH_LEASE_SECONDS"
LEASE_DEFAULT_S = 600


def lease_window_s() -> int:
    """Lease window in seconds. Config, default 600 (consumer claim lease)."""
    try:
        return max(1, int(os.environ.get(LEASE_ENV, str(LEASE_DEFAULT_S))))
    except ValueError:
        return LEASE_DEFAULT_S


class SmtpTransient(Exception):
    """Retryable transport failure (refused/timeout with negative outcome)."""


class SmtpHardError(Exception):
    """Non-retryable failure (auth rejected, malformed). Dead-letters."""


class SendUnknown(Exception):
    """Timeout with UNKNOWN outcome — reconcile by dedupe key, never resend
    blind. Carries the key; the caller must call resolve_unknown first."""

    def __init__(self, idempotency_key: str, detail: str = ""):
        super().__init__(detail or "send outcome unknown")
        self.idempotency_key = idempotency_key


def resolve_unknown(store, idempotency_key: str) -> bool:
    """True = key already effected (suppress resend). Pure read, no I/O
    beyond the Store API. The lease-vs-latency contract hinge."""
    row = store.fetchone(
        "SELECT id FROM actions WHERE idempotency_key=?", (idempotency_key,))
    return row is not None


class SmtpSender(LiveEmailSender):
    """One SMTP implementation, credential sets per config (parity §10 T1).
    Subclass fixes channel + env prefix. IMAP poll included (P1 shape)."""

    channel = ""
    name = "smtp"
    env_prefix = ""

    @property
    def _env_names(self) -> tuple[str, ...]:
        p = self.env_prefix
        return (f"{p}_HOST", f"{p}_PORT", f"{p}_USER", f"{p}_PASS")

    def _smtp_config(self) -> tuple[str, int, str, str]:
        host = os.environ.get(f"{self.env_prefix}_HOST", "")
        try:
            port = int(os.environ.get(f"{self.env_prefix}_PORT", "465"))
        except ValueError:
            raise SmtpHardError("bad SMTP port — fail closed")
        user = os.environ.get(f"{self.env_prefix}_USER", "")
        secret = os.environ.get(f"{self.env_prefix}_PASS", "")
        if not (host and user and secret):
            raise LiveNotConfigured(f"{self.name}: SMTP creds absent at call")
        return host, port, user, secret

    def _transport(self, draft: dict, idempotency_key: str) -> dict:
        import smtplib
        import socket
        host, port, user, secret = self._smtp_config()
        import email.message as _em
        msg = _em.EmailMessage()
        msg["From"] = user
        msg["To"] = draft.get("to", "")
        if not msg["To"]:
            raise SmtpHardError("no recipient — fail closed")
        msg["Subject"] = draft.get("subject", "")
        msg.set_content(draft.get("body", ""))
        try:
            server = smtplib.SMTP_SSL(host, port, timeout=lease_window_s())
        except OSError as exc:
            # Connect-phase failure: nothing was sent (known outcome).
            raise SmtpTransient(f"smtp connect failed: {type(exc).__name__}") from exc
        try:
            with server:
                try:
                    server.login(user, secret)
                except smtplib.SMTPAuthenticationError as exc:
                    raise SmtpHardError(
                        f"smtp auth rejected: {exc.smtp_code}") from exc
                refused = server.send_message(msg)
        except (SmtpHardError, SmtpTransient):
            raise
        except (smtplib.SMTPException, socket.timeout, TimeoutError,
                OSError) as exc:
            # Outcome unknown on timeouts/network drops AFTER the DATA phase
            # may have completed server-side: reconcile, never blind-resend.
            raise SendUnknown(idempotency_key,
                              f"smtp outcome unknown: {type(exc).__name__}") from exc
        if refused:
            raise SmtpTransient(f"smtp refused recipients: {refused}")
        self.last_usage = {"channel": self.channel, "messages": 1,
                           "bytes": len(msg.as_bytes()),
                           "cost_estimate": None,
                           "cost_basis": "provider-metered"}
        return {"refused": {}, "idempotency_key": idempotency_key}

    def poll_inbox(self, limit: int = 25) -> list[dict]:
        """Minimal IMAP poll → envelope list (P1 shape; full ingestion later)."""
        p_paused, reason = self.paused
        if p_paused:
            raise InferencePaused(reason)
        import imaplib
        host = os.environ.get(f"{self.env_prefix}_HOST", "")
        user = os.environ.get(f"{self.env_prefix}_USER", "")
        secret = os.environ.get(f"{self.env_prefix}_PASS", "")
        if not (host and user and secret):
            raise LiveNotConfigured(f"{self.name}: IMAP creds absent")
        try:
            with imaplib.IMAP4_SSL(host, timeout=30) as imap:
                imap.login(user, secret)
                imap.select("INBOX", readonly=True)
                _, data = imap.search(None, "UNSEEN")
                uids = (data[0] or b"").split()[-limit:]
                out = []
                for uid in uids:
                    _, fetched = imap.fetch(uid, "(BODY[HEADER.FIELDS "
                                                 "(FROM SUBJECT MESSAGE-ID)])")
                    out.append({"uid": uid.decode("ascii", "replace"),
                                "raw": fetched[0][1].decode("ascii", "replace")
                                if fetched and fetched[0] else ""})
                return out
        except (imaplib.IMAP4.error, OSError) as exc:
            raise SmtpTransient(f"imap poll failed: {type(exc).__name__}") from exc


class GmailSmtpSender(SmtpSender):
    channel = "email/gmail-smtp"
    name = "gmail-smtp"
    env_prefix = "GMAIL_SMTP"


class ZohoSmtpSender(SmtpSender):
    channel = "email/zoho-smtp"
    name = "zoho-smtp"

    def __init__(self, slot: int = 1):
        super().__init__()
        if slot not in (1, 2, 3, 4):
            raise ValueError("zoho slot must be 01-04")
        self.env_prefix = f"ZOHO_SMTP_0{slot}"
        self.slot = slot


class AgentMailApiSender(LiveEmailSender):
    """AgentMail REST channel (parity §10 T2). Base REQUIRED (no verified
    default); path defaults to /messages marked UNVERIFIED_PENDING_LIVE_DOCS
    — the agentmail first-send protocol MUST verify it against live docs."""

    channel = "email/agentmail-api"
    name = "agentmail-api"
    SEND_PATH_ENV = "AGENTMAIL_SEND_PATH"
    # Default path below is UNVERIFIED_PENDING_LIVE_DOCS — the agentmail
    # first-send protocol MUST verify it against live docs before any real send.

    @property
    def _env_names(self) -> tuple[str, ...]:
        return ("AGENTMAIL_API_BASE_URL", "AGENTMAIL_API_KEY_A")

    def _transport(self, draft: dict, idempotency_key: str) -> dict:
        base = os.environ.get("AGENTMAIL_API_BASE_URL", "").rstrip("/")
        if not base:
            raise LiveNotConfigured("agentmail: AGENTMAIL_API_BASE_URL unset")
        path = os.environ.get(self.SEND_PATH_ENV, "/messages")
        key = os.environ.get("AGENTMAIL_API_KEY_A", "")
        if not key:
            raise LiveNotConfigured("agentmail: key absent at call time")
        body = _json.dumps({"to": draft.get("to"), "subject": draft.get("subject"),
                            "body": draft.get("body"),
                            "idempotency_key": idempotency_key}).encode("utf-8")
        req = _url.Request(
            base + path, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key},
            method="POST")
        try:
            with _url.urlopen(req, timeout=TRANSPORT_TIMEOUT_S) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except LiveNotConfigured:
            raise
        except Exception as exc:
            raise ConnectionError(
                f"agentmail transport failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise ValueError("agentmail: unexpected response shape")
        self.last_usage = {"channel": self.channel, "messages": 1,
                           "provider_id": payload.get("id"),
                           "cost_estimate": None,
                           "cost_basis": "provider-metered"}
        return {"provider_id": payload.get("id"),
                "idempotency_key": idempotency_key}
