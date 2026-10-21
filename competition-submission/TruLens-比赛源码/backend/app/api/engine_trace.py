"""GET /api/engine-trace/{task_id} — the Applied-AI engine's raw execution trace.

Read-only and additive: the frozen ``/api/status`` and ``/api/report`` shapes are
untouched. This endpoint exposes what the engine produces beyond them — the four
visible specialist roles, the full evidence chain with character offsets, the
per-dimension assessments and the engine's own limitations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.exceptions import NotFoundError
from app.models.analysis_task import AnalysisTask
from app.models.engine_trace import EngineTrace
from app.schemas.engine_trace import EngineTraceRead

router = APIRouter(tags=["engine"])


@router.get(
    "/engine-trace/{task_id}",
    response_model=EngineTraceRead,
    summary="获取分析引擎的完整执行轨迹（角色 / 证据链 / 局限）",
)
async def get_engine_trace(
    task_id: str, db: AsyncSession = Depends(get_db)
) -> EngineTraceRead:
    task = await db.get(AnalysisTask, task_id)
    if task is None:
        raise NotFoundError(f"analysis task not found: {task_id}")

    result = await db.execute(select(EngineTrace).where(EngineTrace.task_id == task_id))
    trace = result.scalar_one_or_none()
    if trace is None:
        raise NotFoundError(
            "engine trace not available for this task",
            details={
                "task_id": task_id,
                "reason": (
                    "该任务由内置启发式规则完成，或引擎本次未产出可用结果。"
                    "可通过 /api/health 的 analysis_backend 判断当前后端。"
                ),
            },
        )
    return EngineTraceRead.model_validate(trace)
