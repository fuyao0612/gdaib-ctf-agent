"""评测结果的轻量导出；只导出已持久化的脱敏指标。"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable

from yuwang.domain.evaluation import EvaluationRecord


def records_as_json(records: Iterable[EvaluationRecord]) -> list[dict[str, object]]:
    return [record.model_dump(mode="json") for record in records]


def records_as_csv(records: Iterable[EvaluationRecord]) -> str:
    rows = records_as_json(records)
    fields = [
        "case_id", "case_version", "scenario", "category", "difficulty", "attempt",
        "run_id", "execution_status", "validation_status", "status", "success", "score",
        "max_score", "duration_ms", "model_calls", "provider_requests", "tool_calls",
        "tool_failures", "input_tokens", "output_tokens", "estimated_cost", "retries",
        "replans", "failure_category", "trace_path", "report_path",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def records_as_json_text(records: Iterable[EvaluationRecord]) -> str:
    return json.dumps(records_as_json(records), ensure_ascii=False, indent=2)
