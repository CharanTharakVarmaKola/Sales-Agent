"""inference/base.py — InferenceClient seam + routing (OmniRoute -> FreeLLMAPI)."""
from __future__ import annotations

import abc


class InferenceClient(abc.ABC):
    @abc.abstractmethod
    def draft(self, lead: dict, evidence: list[dict]) -> dict:
        """Return {'subject','body','claims'} or raise."""


class RoutingInferenceClient(InferenceClient):
    """Primary route first, configured fallback on failure."""

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback
        self.last_route = None

    def draft(self, lead, evidence):
        try:
            out = self.primary.draft(lead, evidence)
            self.last_route = {"provider": self.primary.name, "fallback_used": False}
            return out
        except Exception:
            out = self.fallback.draft(lead, evidence)
            self.last_route = {"provider": self.fallback.name, "fallback_used": True}
            return out


class HttpInferenceClient:
    """Live HTTP client — refuses to run without allow_network AND a credential."""

    def __init__(self, name, env_var, allow_network=False):
        self.name, self.env_var, self.allow_network = name, env_var, allow_network

    def draft(self, lead, evidence):
        import os
        if not (self.allow_network and os.environ.get(self.env_var)):
            raise PermissionError(
                f"{self.name}: no credential in {self.env_var} and network disabled — refusing")
        raise NotImplementedError("live transport not enabled in this session")
