#!/usr/bin/env python3
"""InferenceClient interface — the only seam through which the copywriter talks
to a model.

Two rules from the BUILD spec are encoded here:
  * OmniRoute is the PRIMARY route, FreeLLMAPI is the FALLBACK route, and both
    are described *as configuration only* (see providers.py). No HTTP call is
    ever made in this session.
  * Everything the swarm needs to test is reachable through mock clients, so the
    copywriter node can be exercised offline.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class InferenceRequest:
    """A single completion request."""

    prompt: str
    system: str = ""
    purpose: str = "copywriter.draft"
    max_tokens: int = 1024
    temperature: float = 0.2
    response_format: str = "json"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InferenceResult:
    text: str
    provider: str
    model: str
    fallback_used: bool = False
    live: bool = False
    latency_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "fallback_used": self.fallback_used,
            "live": self.live,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


class InferenceError(RuntimeError):
    """Raised when a route cannot produce a completion."""


class InferenceClient(abc.ABC):
    """Contract every route (mock, OmniRoute, FreeLLMAPI) implements."""

    name: str = "inference"
    model: str = "unset"
    live: bool = False

    @abc.abstractmethod
    def complete(self, request: InferenceRequest) -> InferenceResult:
        ...

    def health(self) -> Dict[str, Any]:
        return {"name": self.name, "model": self.model, "live": self.live, "ok": True}


class RoutingInferenceClient(InferenceClient):
    """Primary/fallback router: try `primary`, fall back to `fallback` on error.

    The routing logic is real code; only the two routes underneath it are
    configuration (and, in this session, mocks).
    """

    def __init__(self, primary: InferenceClient, fallback: Optional[InferenceClient] = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"router[{primary.name}" + (f"->{fallback.name}]" if fallback else "]")
        self.model = primary.model
        self.live = bool(primary.live or (fallback.live if fallback else False))
        self.attempts: List[Dict[str, Any]] = []

    def complete(self, request: InferenceRequest) -> InferenceResult:
        try:
            res = self.primary.complete(request)
            self.attempts.append({"provider": self.primary.name, "ok": True})
            res.fallback_used = False
            return res
        except Exception as exc:  # noqa: BLE001 - fallback is the whole point
            self.attempts.append({"provider": self.primary.name, "ok": False, "error": str(exc)})
            if self.fallback is None:
                raise InferenceError(f"primary route {self.primary.name} failed and no fallback configured: {exc}") from exc
            res = self.fallback.complete(request)
            self.attempts.append({"provider": self.fallback.name, "ok": True})
            res.fallback_used = True
            res.error = f"primary {self.primary.name} failed: {exc}"
            return res

    def health(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "primary": self.primary.health(),
            "fallback": self.fallback.health() if self.fallback else None,
            "attempts": self.attempts,
            "live": self.live,
            "ok": True,
        }
