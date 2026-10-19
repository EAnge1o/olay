"""POST /api/analyze/{task_id} and GET /api/status/{task_id}."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_analysis_service, get_db
from app.core.enums import TaskStatus
from app.core.exceptions import ConflictError
from app.schemas.analysis import AnalyzeRequest, AnalyzeResponse, TaskStatusResponse
from app.services.analysis_service import AnalysisService
from app.services.presentation import build_agent_status_items

router = APIRouter(tags=["analysis"])


@router.post(
    "/analyze/{task_id}",
    response_model=AnalyzeResponse,
    summary="启动分析流程（异步）",
)
async def start_analysis(
    task_id: str,
    background: BackgroundTasks,
    payload: AnalyzeRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    analysis_service: AnalysisService = Depends(get_analysis_service),
) -> AnalyzeResponse:
    task = await analysis_service.get_task(db, task_id)

    if task.status == TaskStatus.RUNNING.value:
        raise ConflictError(
            "analysis already running for this task",
            details={"task_id": task_id, "progress": task.progress},
        )

    requested = analysis_service.registry.resolve_subset(payload.agents if payload else None)
    task.requested_agents = requested
    task.run_options = {"use_llm": payload.use_llm if payload else False}
    task.status = TaskStatus.RUNNING.value
    task.progress = 0
    task.error_message = None
    await db.commit()

    background.add_task(analysis_service.run_task, task_id)

    return AnalyzeResponse(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        requested_agents=requested,
        message="analysis started; poll GET /api/status/{task_id}",
    )


@router.get(
    "/status/{task_id}",
    response_model=TaskStatusResponse,
    summary="查看任务与各 Agent 运行状态",
)
async def get_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    analysis_service: AnalysisService = Depends(get_analysis_service),
) -> TaskStatusResponse:
    task, results = await analysis_service.get_status(db, task_id)

    return TaskStatusResponse(
        task_id=task.id,
        sample_id=task.sample_id,
        status=TaskStatus(task.status),
        progress=task.progress,
        requested_agents=list(task.requested_agents or []),
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        report_ready=task.report is not None,
        agents=build_agent_status_items(results),
    )
