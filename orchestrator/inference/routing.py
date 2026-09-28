"""orchestrator/inference/routing.py — pause-first router (B2).

Implements Gate 3 routing: draft() checks primary.paused, then
fallback.paused, and raises InferencePaused(joined_reason) before any I/O
when nothing is unpaused. InferencePaused is never caught-and-swallowed
here — a paused client's own draft() raising it propagates unchanged.

Failover rule: a transient (non-pause) error from the primary fails over
to the fallback ONLY after re-checking fallback.paused. This is the one
place a fallback call follows a primary failure, and the pause re-check
is what keeps a paused provider from ever being dialled. A paused
fallback means the transient error propagates — no silent degradation.

last_route records {"paused", "reason", "primary", "fallback"} on every
draft() exit (success or raise) for audit payloads.

Convention (carried MINOR 1): no I/O in this module beyond delegating to
client draft() calls. Live client classes are out of scope for B2 and
must not be scaffolded here.
"""
from __future__ import annotations

from .base import InferenceClient, InferencePaused


def _key_of(client: InferenceClient | None) -> str | None:
    if client is None:
        return None
    return getattr(client, "name", type(client).__name__)


class RoutingInferenceClient(InferenceClient):
    """Primary-first router with pause-before-network on both legs."""

    name = "routing"

    def __init__(self, primary: InferenceClient,
                 fallback: InferenceClient | None = None):
        self.primary = primary
        self.fallback = fallback
        self.last_route: dict | None = None

    @property
    def paused(self) -> tuple[bool, str]:
        p_paused, p_reason = self.primary.paused
        if not p_paused:
            return (False, "")
        if self.fallback is None:
            return (True, p_reason)
        f_paused, f_reason = self.fallback.paused
        if not f_paused:
            return (False, "")
        return (True, f"{p_reason} | {f_reason}")

    def draft(self, lead: dict, evidence: list[dict]) -> dict:
        primary_key = _key_of(self.primary)
        fallback_key = _key_of(self.fallback)

        p_paused, p_reason = self.primary.paused
        if not p_paused:
            try:
                out = self.primary.draft(lead, evidence)
            except InferencePaused as exc:
                # Pause raced in after the check: record and propagate.
                self.last_route = {"paused": True, "reason": exc.reason,
                                   "primary": primary_key,
                                   "fallback": fallback_key}
                raise
            except Exception:
                # Transient primary failure: fail over only if the
                # fallback is currently unpaused (re-checked here).
                if self.fallback is None:
                    raise
                f_paused, f_reason = self.fallback.paused
                if f_paused:
                    raise
                try:
                    out = self.fallback.draft(lead, evidence)
                except InferencePaused as exc:
                    # Pause raced in after the re-check: record, propagate.
                    self.last_route = {"paused": True, "reason": exc.reason,
                                       "primary": primary_key,
                                       "fallback": fallback_key}
                    raise
                self.last_route = {"paused": False,
                                   "reason": f"primary transient; {f_reason}"
                                             if f_reason else "primary transient",
                                   "primary": primary_key,
                                   "fallback": fallback_key}
                return out
            self.last_route = {"paused": False, "reason": "",
                               "primary": primary_key,
                               "fallback": fallback_key}
            return out

        # Primary paused: consult fallback (pause state only, no I/O yet).
        if self.fallback is None:
            self.last_route = {"paused": True, "reason": p_reason,
                               "primary": primary_key, "fallback": None}
            raise InferencePaused(p_reason)
        f_paused, f_reason = self.fallback.paused
        if f_paused:
            joined = f"{p_reason} | {f_reason}"
            self.last_route = {"paused": True, "reason": joined,
                               "primary": primary_key,
                               "fallback": fallback_key}
            raise InferencePaused(joined)
        try:
            out = self.fallback.draft(lead, evidence)
        except InferencePaused as exc:
            # Pause raced in after the check: record and propagate.
            self.last_route = {"paused": True, "reason": exc.reason,
                               "primary": primary_key,
                               "fallback": fallback_key}
            raise
        self.last_route = {"paused": False, "reason": p_reason,
                           "primary": primary_key,
                           "fallback": fallback_key}
        return out
