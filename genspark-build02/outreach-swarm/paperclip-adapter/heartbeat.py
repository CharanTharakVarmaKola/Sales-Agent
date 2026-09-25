"""paperclip-adapter/heartbeat.py — activity-record + heartbeat adapter.

Records every agent run as a Paperclip activity record (mock/local storage in
this session) and emits a heartbeat per run. Incident emission is DEDUPLICATED:
the same (agent_id, incident_key) produces exactly one incident note, ever —
this is the dedupe logic the watchdog self-test reuses.
"""
from __future__ import annotations

import json
import os
import time


class PaperclipAdapter:
    def __init__(self, store_path: str):
        self.store_path = store_path
        os.makedirs(os.path.dirname(store_path) or ".", exist_ok=True)
        self._records: list[dict] = []
        self._emitted_incidents: set[tuple] = set()
        self._notifications: list[dict] = []

    # ------------------------------------------------------ activity records
    def record(self, agent_id: str, event: str, payload: dict | None = None) -> dict:
        rec = {"agent_id": agent_id, "event": event,
               "ts": time.time(), "payload": payload or {}}
        self._records.append(rec)
        self._persist()
        return rec

    def heartbeat(self, agent_id: str) -> dict:
        return self.record(agent_id, "heartbeat", {"alive": True})

    def records(self, agent_id: str | None = None) -> list[dict]:
        if agent_id is None:
            return list(self._records)
        return [r for r in self._records if r["agent_id"] == agent_id]

    # ------------------------------------------------------ incident path
    def emit_incident(self, lead_note_path: str, agent_id: str, incident_key: str,
                      text: str) -> dict | None:
        """Exactly one incident note per (agent_id, incident_key). Returns the
        written incident dict the first time, None on every duplicate."""
        key = (agent_id, incident_key)
        if key in self._emitted_incidents:
            return None
        self._emitted_incidents.add(key)
        incident_id = f"INC-{agent_id}-{incident_key}"
        from orchestrator.policy import reporter
        reporter.write_incident_note(lead_note_path, incident_id, text)
        notification = {"incident_id": incident_id, "agent_id": agent_id,
                        "incident_key": incident_key, "text": text}
        self._notifications.append(notification)
        return notification

    def notifications(self) -> list[dict]:
        return list(self._notifications)

    def _persist(self):
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(self._records, fh, default=str)
