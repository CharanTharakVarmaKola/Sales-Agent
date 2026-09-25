"""inference/providers.py — OmniRoute primary / FreeLLMAPI fallback as CONFIGURATION."""
from __future__ import annotations

import os

from .base import HttpInferenceClient, RoutingInferenceClient


def get_client(allow_network: bool = False):
    from .mock import FailingClient, MockInferenceClient
    if allow_network:
        return RoutingInferenceClient(
            HttpInferenceClient("omniroute", "OMNIROUTE_API_KEY", allow_network=True),
            HttpInferenceClient("mock-freellmapi", "FREELLMAPI_API_KEY", allow_network=True))
    # offline session: mock primary, failing fallback so failover logic is exercised
    return RoutingInferenceClient(HttpInferenceClient("omniroute", "OMNIROUTE_API_KEY"),
                                  MockInferenceClient())


def get_failing_primary_client():
    """Router whose primary always fails — proves the fallback fires."""
    from .mock import FailingClient, MockInferenceClient
    return RoutingInferenceClient(FailingClient(), MockInferenceClient())
