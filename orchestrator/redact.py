"""orchestrator/redact.py — redaction-before-transact (B6-PREP, CRITICAL).

Implements docs/parity-B6.md §4. The audit chain is append-only: anything
written into a payload is permanent. Every live-path payload passes
redact_for_audit() before Store.transact(). Pure function, no I/O.
"""
from __future__ import annotations

import re

REDACTED = "***REDACTED***"

_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|api[_-]?secret|client[_-]?secret|"
               r"refresh[_-]?token|access[_-]?token|auth[_-]?token|"
               r"private[_-]?key|secret|passwd|password)\s*['\":=]+\s*"
               r"['\"]?([^'\"\s,}]+)"),
    re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9\-._~+/]+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
               re.DOTALL),
    re.compile(r"\b(sk-live|sk-test|am_)[A-Za-z0-9\-_]+"),
)


def _redact_str(text: str) -> str:
    def _one(m: "re.Match") -> str:
        if m.lastindex is None:
            return REDACTED
        return _mask(m)

    for pat in _PATTERNS:
        text = pat.sub(_one, text)
    return text


def _mask(m: "re.Match") -> str:
    full = m.group(0)
    secret = m.group(m.lastindex)
    return full.replace(secret, REDACTED)


def redact_for_audit(obj):
    """Recursively redact credential-shaped values. Pure, no I/O."""
    if isinstance(obj, str):
        return _redact_str(obj)
    if isinstance(obj, dict):
        return {k: redact_for_audit(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_for_audit(v) for v in obj]
    return obj
