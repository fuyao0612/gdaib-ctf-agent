"""按序读取运行事件，为轮询与 SSE 重连提供一致游标语义。"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from yuwang.domain.models import Event, EventType
from yuwang.policy import redact


class EventService:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def emit(
        self,
        run_id: UUID,
        event_type: EventType,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        clean_summary = redact(summary)
        clean_payload = self._redact_value(payload or {})
        return cast(
            Event,
            self.repository.create_event(run_id, event_type, clean_summary, clean_payload),
        )

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if key.lower() in {"api_key", "token", "password", "secret"}
                else self._redact_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        return value
