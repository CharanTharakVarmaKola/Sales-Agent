"""orchestrator/inference/dry_run.py — always-paused dry-run client (B2).

Implements Gate 3 dry-run pole: paused is always (True, reason) and
draft() always raises InferencePaused. There is deliberately no network
path in this class — not even dead code — so nothing to audit for URLs,
ports, or transports here.

Convention (carried MINOR 1): no I/O anywhere in this module.
"""
from __future__ import annotations

from .base import InferenceClient, InferencePaused

DRY_RUN_REASON = "dry_run: awaiting creds"


class DryRunInferenceClient(InferenceClient):
    """Pre-credential stand-in. Never fabricates a draft, never dials out."""

    name = "dry_run"

    @property
    def paused(self) -> tuple[bool, str]:
        return (True, DRY_RUN_REASON)

    def draft(self, lead: dict, evidence: list[dict]) -> dict:
        raise InferencePaused(DRY_RUN_REASON)
