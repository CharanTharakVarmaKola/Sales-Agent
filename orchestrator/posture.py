"""orchestrator/posture.py — live posture flag + kill-switch + channels (B6-PREP).

Implements docs/parity-B6.md §§5-6. One flag (OUTREACH_LIVE=1) enables;
anything else is dry_run. is_live() reads env on EVERY call (no caching —
caching would delay the kill). Runs sample posture ONCE at start and
complete under it. Channel status reports names-and-booleans only.
No values are logged, stored, or returned here. Dry-run posture holds
until per-credential unlock in B6-LIVE.
"""
from __future__ import annotations

import os

LIVE_FLAG_ENV = "OUTREACH_LIVE"

CHANNELS: dict[str, tuple[str, ...]] = {
    "llm/omniroute": ("OMNIROUTE_API_KEY",),
    "llm/freellmapi": ("FREELLMAPI_API_KEY",),
    "email/gmail": ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
                    "GMAIL_REFRESH_TOKEN"),
    "email/zoho": tuple(
        f"ZOHO_{k}_0{i}" for i in (1, 2, 3, 4)
        for k in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN",
                  "ACCOUNT_EMAIL")),
    "email/agentmail": ("AGENTMAIL_API_KEY_A",),
    # STAGE P1 transport law (parity-B6 §10): SMTP credential sets.
    # Appended, never reordered; OAuth names above stay the live-session path.
    "email/gmail-smtp": ("GMAIL_SMTP_HOST", "GMAIL_SMTP_PORT",
                         "GMAIL_SMTP_USER", "GMAIL_SMTP_PASS"),
    "email/zoho-smtp": tuple(
        f"ZOHO_SMTP_0{i}_{k}" for i in (1, 2, 3, 4)
        for k in ("HOST", "PORT", "USER", "PASS")),
    "email/agentmail-api": ("AGENTMAIL_API_BASE_URL", "AGENTMAIL_API_KEY_A"),
    # Keyless local channel: presence of the base URL is the credential.
    # No secret exists for this channel (explicit no-auth, never a dummy key).
    "llm/ollama": ("OLLAMA_BASE_URL",),
}

# Deprecated, never a live read path (parity §6 P-B6-9).
DEPRECATED_ENV = ("AGENTMAIL_API_KEY",) + tuple(
    f"ZOHO_{k}_05" for k in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN",
                             "ACCOUNT_EMAIL"))


def is_live() -> bool:
    """Kill-switch read. Uncached by design."""
    return os.environ.get(LIVE_FLAG_ENV) == "1"


def posture_snapshot() -> dict:
    """Sampled once per run; in-flight runs complete under it."""
    return {"live": is_live()}


def channel_status() -> dict[str, dict[str, bool]]:
    """Per-channel activation inputs. Booleans only — never values."""
    live = is_live()
    status: dict[str, dict[str, bool]] = {}
    for channel, names in CHANNELS.items():
        configured = all(os.environ.get(n) for n in names)
        status[channel] = {"configured": bool(configured),
                           "live_unlocked": bool(live and configured)}
    return status
