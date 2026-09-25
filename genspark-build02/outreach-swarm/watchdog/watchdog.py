"""watchdog/watchdog.py — monitors the Paperclip feed and raises notifications.

Subscribes to the adapter's incident emissions. Dedupe is inherited from the
adapter: one incident -> exactly one watchdog notification.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class Watchdog:
    def __init__(self, adapter):
        self.adapter = adapter
        self.alerts: list[dict] = []
        self.adapter_record = adapter.record("watchdog", "subscribe",
                                             {"watches": "incident emissions"})

    def on_incident(self, notification: dict | None) -> int:
        """Fire on an adapter notification. Duplicate (None) fires nothing."""
        if notification is None:
            return 0
        self.alerts.append(notification)
        return 1

    def check(self, lead_note_path: str, agent_id: str, incident_key: str,
              text: str) -> int:
        """Full path: adapter dedupe -> watchdog notification. Returns 0 or 1."""
        notification = self.adapter.emit_incident(lead_note_path, agent_id,
                                                  incident_key, text)
        return self.on_incident(notification)

    def alert_count(self) -> int:
        return len(self.alerts)


def wire_into_run_loop(adapter, watchdog) -> None:
    """Called from the orchestrator's main run loop (see orchestrator/run_loop.py):
    every agent run heartbeats; every failed run raises one incident."""
    adapter.heartbeat("run-loop")
