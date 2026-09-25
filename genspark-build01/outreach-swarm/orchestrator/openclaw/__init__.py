"""OpenClaw seam (local browser/automation layer used by ingest)."""
from .client import (
    HEADER_ALIASES,
    LiveOpenClawClient,
    MockOpenClawClient,
    OpenClawClient,
    OpenClawUnavailable,
    build_openclaw_client,
)

__all__ = [
    "OpenClawClient",
    "MockOpenClawClient",
    "LiveOpenClawClient",
    "OpenClawUnavailable",
    "HEADER_ALIASES",
    "build_openclaw_client",
]
