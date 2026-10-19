"""Report retrieval — read side of the pipeline."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import TaskStatus
from app.core.exceptions import NotFoundError
from app.models.agent_result import AgentResult
from app.models.analysis_task import AnalysisTask
from app.models.report import Report


class ReportService:
    async def get_report(self, db: AsyncSession, task_id: str) -> Report:
        """Return the report for a task, with precise, actionable failures."""
        task = await db.get(AnalysisTask, task_id)
        if task is None:
            raise NotFoundError(f"analysis task not found: {task_id}")

        if task.status == TaskStatus.FAILED.value:
            raise NotFoundError(
                "analysis task failed; no report generated",
                details={"task_id": task_id, "error": task.error_message},
            )

        result = await db.execute(select(Report).where(Report.task_id == task_id))
        report = result.scalar_one_or_none()
        if report is None:
            raise NotFoundError(
                "report not ready yet",
                details={"task_id": task_id, "status": task.status},
            )
        return report

    async def get_agent_results(self, db: AsyncSession, task_id: str) -> list[AgentResult]:
        """The per-dimension rows behind the report, in canonical display order."""
        result = await db.execute(
            select(AgentResult).where(AgentResult.task_id == task_id)
        )
        return list(result.scalars().all())

    async def list_reports(self, db: AsyncSession, limit: int = 20) -> list[Report]:
        result = await db.execute(
            select(Report).order_by(Report.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
