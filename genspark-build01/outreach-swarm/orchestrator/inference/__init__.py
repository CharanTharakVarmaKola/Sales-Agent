"""Inference seam for the copywriter node (primary: OmniRoute, fallback: FreeLLMAPI)."""
from .base import InferenceClient, InferenceError, InferenceRequest, InferenceResult, RoutingInferenceClient
from .mock import MockInferenceClient
from .providers import (
    FREELMAPI,
    OMNIROUTE,
    HttpInferenceClient,
    ProviderSpec,
    build_inference_client,
    describe_routes,
)

__all__ = [
    "InferenceClient",
    "InferenceError",
    "InferenceRequest",
    "InferenceResult",
    "RoutingInferenceClient",
    "MockInferenceClient",
    "ProviderSpec",
    "HttpInferenceClient",
    "OMNIROUTE",
    "FREELMAPI",
    "build_inference_client",
    "describe_routes",
]
