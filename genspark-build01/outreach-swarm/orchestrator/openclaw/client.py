#!/usr/bin/env python3
"""OpenClaw client seam.

In this design OpenClaw is the local browser/automation layer used for two
ingest-side jobs that plain CSV parsing cannot do:

1. ``normalize_columns`` — map the wildly different header spellings found in
   directory and map-style exports ("Company Name", "ORG", "company_name") onto
   the normalized lead fields.
2. ``enrich_lead`` — fill a genuinely missing field (industry, country, title)
   from a local profile/Markdown source the operator already has.

Neither job is exercised against a live OpenClaw install in this session — the
credential-free test path uses ``MockOpenClawClient``. The upstream citation for
OpenClaw lives in the compatibility report and is not re-derived here.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence


class OpenClawUnavailable(RuntimeError):
    """Raised when a live OpenClaw call is attempted without a local install."""


class OpenClawClient:
    """Interface: live implementations talk to a local OpenClaw instance."""

    name = "openclaw"
    live = False

    def normalize_columns(self, headers: Sequence[str]) -> Dict[str, str]:
        raise NotImplementedError

    def enrich_lead(self, record: Mapping[str, Any], missing: Sequence[str]) -> Dict[str, Any]:
        raise NotImplementedError

    def health(self) -> Dict[str, Any]:
        return {"name": self.name, "live": self.live, "ok": True}


#: Canonical field <- accepted header spellings (lower-cased, punctuation-stripped).
HEADER_ALIASES: Dict[str, tuple[str, ...]] = {
    "name": ("name", "full name", "contact", "contact name", "person", "first name", "lead name"),
    "title": ("title", "job title", "role", "position", "seniority"),
    "company": ("company", "company name", "org", "organization", "organisation", "employer", "business"),
    "domain": ("domain", "website", "company website", "url", "site", "web"),
    "email": ("email", "e-mail", "email address", "e mail address", "work email", "mail", "contact email"),
    "country": ("country", "country code", "location country", "market"),
    "industry": ("industry", "sector", "vertical", "category", "naics", "sic"),
    "pain_point": ("pain point", "pain", "challenge", "problem", "need", "signal", "reason"),
    "angle": ("angle", "hook", "offer", "value proposition", "approach", "next step"),
    "unsubscribed": ("unsubscribed", "opt out", "optout", "do not contact", "dnc", "suppressed"),
}

_NORM = re.compile(r"[^a-z0-9]+")


def _norm_header(h: str) -> str:
    return _NORM.sub(" ", str(h or "").strip().lower()).strip()


class MockOpenClawClient(OpenClawClient):
    """Deterministic stand-in: alias matching + conservative enrichment."""

    def __init__(self, *, enrich_country: str = "", enrich_industry: str = "") -> None:
        self.enrich_country = enrich_country
        self.enrich_industry = enrich_industry
        self.calls: List[str] = []

    def normalize_columns(self, headers: Sequence[str]) -> Dict[str, str]:
        self.calls.append("normalize_columns")
        mapping: Dict[str, str] = {}
        for canonical, aliases in HEADER_ALIASES.items():
            for h in headers:
                if _norm_header(h) in aliases and canonical not in mapping:
                    mapping[canonical] = h
                    break
        return mapping

    def enrich_lead(self, record: Mapping[str, Any], missing: Sequence[str]) -> Dict[str, Any]:
        self.calls.append("enrich_lead")
        out = dict(record)
        for field in missing:
            if field == "country" and self.enrich_country:
                out["country"] = self.enrich_country
            elif field == "industry" and self.enrich_industry:
                out["industry"] = self.enrich_industry
        return out


class LiveOpenClawClient(OpenClawClient):
    """Real client — refuses to run without a local OpenClaw endpoint."""

    def __init__(self, endpoint: str = "") -> None:
        self.endpoint = endpoint
        if not endpoint:
            raise OpenClawUnavailable(
                "no OpenClaw endpoint configured; this session has no local install "
                "and no credentials — use MockOpenClawClient for tests"
            )
        self.live = True  # pragma: no cover


def build_openclaw_client(mode: str = "mock", **kw: Any) -> OpenClawClient:
    if mode == "live":
        return LiveOpenClawClient(kw.get("endpoint", ""))
    return MockOpenClawClient(
        enrich_country=kw.get("enrich_country", ""),
        enrich_industry=kw.get("enrich_industry", ""),
    )
