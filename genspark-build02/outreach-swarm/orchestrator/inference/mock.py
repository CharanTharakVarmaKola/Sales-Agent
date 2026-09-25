"""inference/mock.py — grounded / hallucinating / malformed mocks."""
from __future__ import annotations

from .base import InferenceClient


class MockInferenceClient(InferenceClient):
    name = "mock"

    def draft(self, lead, evidence):
        refs = [e["ref"] for e in evidence]
        return {
            "subject": f"{lead.get('company', 'your team')} — one grounded idea",
            "body": f"Hi {lead.get('name', 'there')}, see evidence below.",
            "claims": [{"text": f"{lead.get('company', 'your segment')} teams report this pattern",
                        "evidence_refs": refs[:1] or ["vault/Evidence.md"]}],
        }


class HallucinatingMockClient(InferenceClient):
    name = "mock-hallucinating"

    def draft(self, lead, evidence):
        return {
            "subject": "s", "body": "b",
            "claims": [{"text": "we 3x'd revenue with no source", "evidence_refs": []}],
        }


class MalformedMockClient(InferenceClient):
    name = "mock-malformed"

    def draft(self, lead, evidence):
        raise RuntimeError("model returned non-JSON output")


class FailingClient(InferenceClient):
    name = "mock-freellmapi"

    def draft(self, lead, evidence):
        raise ConnectionError("primary route down")
