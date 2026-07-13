"""运行报告预览与下载路由。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from apps.api.context import ApiContext


def create_report_router(context: ApiContext) -> APIRouter:
    """创建 Markdown/JSON 报告的三个稳定入口。"""

    router = APIRouter(prefix="/api/v1/runs", tags=["reports"])

    @router.get("/{run_id}/report")
    async def report_preview(run_id: UUID) -> dict[str, Any]:
        context.require_run(run_id)
        report = context.repository.get_report(run_id)
        if not report:
            raise HTTPException(404, "报告尚未生成")
        return {"markdown": report[0], "data": report[1]}

    @router.get("/{run_id}/report.md")
    async def report_markdown(run_id: UUID) -> PlainTextResponse:
        report = context.repository.get_report(run_id)
        if not report:
            raise HTTPException(404, "报告尚未生成")
        return PlainTextResponse(
            report[0],
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="report-{run_id}.md"'},
        )

    @router.get("/{run_id}/report.json")
    async def report_json(run_id: UUID) -> JSONResponse:
        report = context.repository.get_report(run_id)
        if not report:
            raise HTTPException(404, "报告尚未生成")
        return JSONResponse(
            report[1],
            headers={"Content-Disposition": f'attachment; filename="report-{run_id}.json"'},
        )

    return router
