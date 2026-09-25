#!/usr/bin/env python3
"""Inference routes as CONFIGURATION ONLY.

OmniRoute is the primary route and FreeLLMAPI is the fallback route in the
design, but nothing in this project makes a live call in this session:

* ``HttpInferenceClient`` is written so the wiring is real, but it refuses to
  run unless ``allow_network=True`` *and* the credential env var is present.
  In this session neither is true, so any attempt raises
  ``InferenceError("live route disabled")`` and is recorded, not silently
  swallowed.
* ``build_inference_client()`` therefore returns mock or router-over-mock
  clients for every test in this repo.

The compatibility report's citations for the two upstream projects are held in
that report; they are not re-derived here (BUILD spec: do not re-research).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .base import InferenceClient, InferenceError, InferenceRequest, InferenceResult, RoutingInferenceClient
from .mock import MockInferenceClient


@dataclass(frozen=True)
class ProviderSpec:
    """Everything needed to reach a route — description only, not a call."""

    name: str
    role: str  # 'primary' | 'fallback'
    base_url: str
    model: str
    api_key_env: str
    notes: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env)

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "api_key_present": bool(self.api_key),
            "notes": self.notes,
        }


#: PRIMARY — local-first OpenAI-compatible gateway.
OMNIROUTE = ProviderSpec(
    name="omniroute",
    role="primary",
    base_url=os.environ.get("OMNIROUTE_BASE_URL", "http://127.0.0.1:8787/v1"),
    model=os.environ.get("OMNIROUTE_MODEL", "qwen2.5-7b-instruct"),
    api_key_env="OMNIROUTE_API_KEY",
    notes="primary route; OpenAI-compatible /chat/completions, no external spend",
)

#: FALLBACK — free-tier hosted API, used only when OmniRoute errors out.
FREELMAPI = ProviderSpec(
    name="freellmapi",
    role="fallback",
    base_url=os.environ.get("FREELLMAPI_BASE_URL", "https://api.freellmapi.example/v1"),
    model=os.environ.get("FREELLMAPI_MODEL", "free-tier-chat"),
    api_key_env="FREELLMAPI_API_KEY",
    notes="fallback route; only reached when the primary route raises",
)


class HttpInferenceClient(InferenceClient):
    """Real HTTP route — deliberately not exercised in this session."""

    def __init__(self, spec: ProviderSpec, *, allow_network: bool = False, timeout: float = 30.0) -> None:
        self.spec = spec
        self.name = spec.name
        self.model = spec.model
        self.allow_network = bool(allow_network) and bool(spec.api_key)
        self.live = self.allow_network
        self.timeout = timeout
        self.calls: list[Dict[str, Any]] = []

    def complete(self, request: InferenceRequest) -> InferenceResult:
        record = {"provider": self.name, "model": self.model, "purpose": request.purpose, "at": time.time()}
        self.calls.append(record)
        if not self.allow_network:
            raise InferenceError(
                f"live route disabled for {self.name}: this session has no credentials "
                f"({self.spec.api_key_env} unset) and network calls are not permitted"
            )
        body = json.dumps(
            {
                "model": self.model,
                "messages": ([{"role": "system", "content": request.system}] if request.system else [])
                + [{"role": "user", "content": request.prompt}],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.spec.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.spec.api_key}"},
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # pragma: no cover
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as exc:
            raise InferenceError(f"{self.name} request failed: {exc}") from exc
        text = (
            payload.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(payload, dict)
            else ""
        )
        return InferenceResult(
            text=text, provider=self.name, model=self.model, live=True,
            latency_ms=round((time.time() - t0) * 1000, 2),
        )


def describe_routes() -> Dict[str, Any]:
    """Configuration summary — safe to print, contains no secrets."""
    return {"primary": OMNIROUTE.describe(), "fallback": FREELMAPI.describe()}


def build_inference_client(
    mode: str = "mock",
    *,
    mock_mode: str = "grounded",
    fail_primary: bool = False,
    allow_network: bool = False,
) -> InferenceClient:
    """Factory used by the graph.

    mode='mock'  -> mock router (OmniRoute-shaped primary, FreeLLMAPI-shaped
                    fallback) so the fallback path itself is testable offline.
    mode='live'  -> real HttpInferenceClient routes; still refuses to call out
                    unless allow_network and credentials are both present.
    """
    if mode == "live":
        primary = HttpInferenceClient(OMNIROUTE, allow_network=allow_network)
        fallback = HttpInferenceClient(FREELMAPI, allow_network=allow_network)
        return RoutingInferenceClient(primary, fallback)

    primary = MockInferenceClient(mock_mode, name=f"mock-{OMNIROUTE.name}", fail=fail_primary)
    fallback = MockInferenceClient("grounded", name=f"mock-{FREELMAPI.name}")
    return RoutingInferenceClient(primary, fallback)
