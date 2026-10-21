"""GET /api/report/{task_id} — the final credibility report."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_report_service
from app.schemas.report import ReportRead
from app.services.report_intelligence import build_report_read
from app.services.report_service import ReportService

router = APIRouter(tags=["report"])


@router.get(
    "/report/{task_id}",
    response_model=ReportRead,
    summary="获取最终可信度报告（四维 T/R/U/S）",
)
async def get_report(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    report_service: ReportService = Depends(get_report_service),
) -> ReportRead:
    report = await report_service.get_report(db, task_id)
    # The four-dimension view is assembled from the per-agent rows, not stored on
    # the report row, so a re-read never drifts from what /api/status showed.
    rows = await report_service.get_agent_results(db, task_id)
    return build_report_read(report, rows)
