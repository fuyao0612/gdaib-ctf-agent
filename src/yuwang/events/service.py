from __future__ import annotations

from typing import Any
from uuid import UUID

from yuwang.domain.models import Event, EventType
from yuwang.policy import redact
from yuwang.storage import SQLiteRepository


class EventService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def emit(self, run_id: UUID, event_type: EventType, summary: str, payload: dict[str, Any] | None = None) -> Event:
        clean_summary = redact(summary)
        clean_payload = self._redact_value(payload or {})
        event = Event(run_id=run_id, sequence=self.repository.next_sequence(run_id), type=event_type, summary=clean_summary, payload=clean_payload)
        return self.repository.append_event(event)

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {key: "[REDACTED]" if key.lower() in {"api_key", "token", "password", "secret"} else self._redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        return value
