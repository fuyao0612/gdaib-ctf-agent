"""运行报告预览与下载路由。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from apps.api.context import ApiContext
from yuwang.reports.generator import ReportGenerator
from yuwang.reports.trace import RunTraceService


def create_report_router(context: ApiContext) -> APIRouter:
    """创建 Markdown/JSON 报告的三个稳定入口。"""

    router = APIRouter(prefix="/api/v1/runs", tags=["reports"])

    def render_persisted_report(run_id: UUID) -> tuple[str, dict[str, Any]]:
        """Re-render legacy reports from persisted facts without changing a Run or executing tools."""
        run = context.require_run(run_id)
        task = context.repository.get_run_task(run_id)
        if not task:
            raise HTTPException(404, "运行缺少任务快照，无法生成报告")
        previous = context.repository.get_report(run_id)
        previous_data = previous[1] if previous else {}
        trace = RunTraceService(context.repository).snapshot(run_id)
        return ReportGenerator().generate(
            run, task, context.repository.list_events(run_id),
            {
                "trace": trace,
                "validation_status": run.validation_status,
                "evidence_level": run.evidence_level,
                "evidence_records": trace.get("evidence", []),
                # A historical final answer may only live in its older report JSON.
                "final_answer": previous_data.get("final_answer"),
            },
        )

    @router.get("/{run_id}/report")
    async def report_preview(run_id: UUID) -> dict[str, Any]:
        markdown, data = render_persisted_report(run_id)
        return {"markdown": markdown, "data": data}

    @router.get("/{run_id}/report.md")
    async def report_markdown(run_id: UUID) -> PlainTextResponse:
        markdown, _ = render_persisted_report(run_id)
        return PlainTextResponse(
            markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="report-{run_id}.md"'},
        )

    @router.get("/{run_id}/report.json")
    async def report_json(run_id: UUID) -> JSONResponse:
        _, data = render_persisted_report(run_id)
        return JSONResponse(
            data,
            headers={"Content-Disposition": f'attachment; filename="report-{run_id}.json"'},
        )

    @router.get("/{run_id}/trajectory.json")
    async def trajectory_json(run_id: UUID) -> JSONResponse:
        """导出只读回放所需的脱敏运行轨迹，不包含 Artifact 内容或存储路径。"""

        context.require_run(run_id)
        return JSONResponse(
            RunTraceService(context.repository).snapshot(run_id),
            headers={"Content-Disposition": f'attachment; filename="trajectory-{run_id}.json"'},
        )

    return router
