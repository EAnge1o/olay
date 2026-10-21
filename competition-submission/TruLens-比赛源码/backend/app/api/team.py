"""Team verification queue endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.team import TeamQueueRead, TeamReviewUpdate
from app.services.team_service import TeamService

router = APIRouter(prefix="/team", tags=["team"])
_team_service = TeamService()


@router.get(
    "/queue",
    response_model=TeamQueueRead,
    summary="获取真实团队核验队列与关联分组",
)
async def get_team_queue(
    limit: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> TeamQueueRead:
    return await _team_service.get_queue(db, limit=limit)


@router.patch(
    "/queue/{task_id}",
    summary="更新团队核验处理状态",
)
async def update_team_status(
    task_id: str,
    payload: TeamReviewUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    status = await _team_service.update_status(db, task_id, payload.status)
    return {"task_id": task_id, "status": status}
