"""openclaw/client.py — seam for header normalization + enrichment.

Maps real directory-export header drift onto the canonical Lead.md fields.
No network. No skill-marketplace references — verified by the phase0
reference scanner.
"""
from __future__ import annotations

ALIASES = {
    "name": ["name", "full name", "fullname", "contact", "person", "contact name", "lead"],
    "company": ["company", "org", "organization", "employer", "account", "firm"],
    "email": ["email", "e-mail", "email address", "mail", "work email"],
    "title": ["title", "role", "job title", "position"],
    "industry": ["industry", "sector", "vertical", "segment"],
}

FORBIDDEN_INPUT_COLUMNS = {"run_id", "final_status", "send_count"}


def normalize_headers(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Return ({header: canonical}, [dropped columns])."""
    mapping, dropped = {}, []
    for h in headers:
        hl = (h or "").strip().lower()
        if hl in FORBIDDEN_INPUT_COLUMNS:
            dropped.append(h)
            continue
        for canonical, aliases in ALIASES.items():
            if hl in aliases:
                mapping[h] = canonical
                break
        else:
            mapping[h] = hl.replace(" ", "_") or "unknown"
    return mapping, dropped


def enrich(lead: dict) -> dict:
    """Deterministic local enrichment — fills blanks, invents nothing."""
    lead.setdefault("industry", "")
    if not lead.get("email"):
        lead["email"] = ""
    return lead
