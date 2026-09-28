"""orchestrator/inference/base.py — InferenceClient ABC + pause signal (B2).

Implements Gate 3: inference_paused is a formal part of the interface.
Every client exposes paused -> (is_paused, reason); every draft() path
checks pause state before any network I/O and raises InferencePaused.

Convention (carried MINOR 1): no I/O inside pause checks or routing
decisions. paused properties must be pure (no network, no file reads).

B4 caller contract: catch InferencePaused explicitly BEFORE any broad
except Exception. InferencePaused carries .reason for audit; a generic
handler that swallows it would hide a spend-cap/allowlist outage, so
orchestrator node code must handle it by name first.
"""
from __future__ import annotations

import abc


class InferencePaused(Exception):
    """Raised when inference is paused (spend-cap, allowlist, outage).

    Carries .reason for audit payloads. Handlers must catch this class
    explicitly before any broad except Exception.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class InferenceClient(abc.ABC):
    """Pluggable LLM seam. Live clients slot in with zero orchestration
    changes once gateway access lands; dry-run clients stay paused."""

    name = "inference-client"

    @abc.abstractmethod
    def draft(self, lead: dict, evidence: list[dict]) -> dict:
        """Return {'subject','body','claims'} or raise (incl. InferencePaused)."""

    @property
    @abc.abstractmethod
    def paused(self) -> tuple[bool, str]:
        """(is_paused, reason). Pure: no I/O. Checked before any network."""
