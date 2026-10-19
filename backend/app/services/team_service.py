"""Database-backed team verification queue and review status."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import TaskStatus
from app.core.exceptions import NotFoundError
from app.core.utils import utcnow
from app.models.analysis_task import AnalysisTask
from app.models.content_sample import ContentSample
from app.models.report import Report
from app.models.team_review import TeamReview
from app.schemas.report import DimensionRead
from app.schemas.team import (
    TeamCheckRead,
    TeamGroupMemberRead,
    TeamQueueItemRead,
    TeamQueueRead,
    TeamRelatedGroupRead,
    TeamSummaryRead,
)
from app.services.report_intelligence import build_report_read
from app.services.report_service import ReportService


class TeamService:
    def __init__(self, report_service: ReportService | None = None) -> None:
        self._report_service = report_service or ReportService()

    async def get_queue(self, db: AsyncSession, limit: int = 100) -> TeamQueueRead:
        result = await db.execute(
            select(AnalysisTask, Report, ContentSample)
            .join(Report, Report.task_id == AnalysisTask.id)
            .join(ContentSample, ContentSample.id == AnalysisTask.sample_id)
            .where(AnalysisTask.status == TaskStatus.COMPLETED.value)
            .order_by(Report.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        queue_rows = list(result.all())
        review_result = await db.execute(select(TeamReview))
        statuses = {review.task_id: review.status for review in review_result.scalars().all()}

        items: list[TeamQueueItemRead] = []
        read_by_task = {}
        for task, report, sample in queue_rows:
            agent_rows = await self._report_service.get_agent_results(db, task.id)
            report_read = build_report_read(report, agent_rows)
            read_by_task[task.id] = report_read
            dimension_by_code = {dimension.code: dimension for dimension in report_read.dimensions}
            assessed = [
                dimension for dimension in report_read.dimensions if dimension.assessed
            ]
            top_dimension = max(
                assessed,
                key=lambda dimension: dimension.risk_score
                if dimension.risk_score is not None
                else -1,
                default=None,
            )
            issue_type = _issue_type(top_dimension)
            confidence = report_read.confidence.level if report_read.confidence else "low"
            default_status = (
                "pending"
                if report.risk_level == "high"
                else "watching"
                if report.risk_level == "medium"
                else "done"
            )
            status = statuses.get(task.id, default_status)
            contradiction = _contradiction(report_read.cross_checks, report_read.decision)
            items.append(
                TeamQueueItemRead(
                    id=task.id,
                    task_id=task.id,
                    sample_id=sample.id,
                    title=_sample_title(sample),
                    author=(sample.account_snapshot.handle if sample.account_snapshot else None)
                    or "未提供账号",
                    score=round(report.trust_score, 1),
                    risk_level=report.risk_level,
                    confidence=confidence,
                    dimensions=[dimension.code for dimension in assessed],
                    contradiction=contradiction,
                    issue_type=issue_type,
                    actions=_actions(report.risk_level, issue_type),
                    status=status,
                    checks=_checks(assessed),
                    created_at=report.created_at,
                )
            )

        return TeamQueueRead(
            items=items,
            summary=_summary(items),
            groups=_related_groups(queue_rows, items),
            generated_at=utcnow(),
        )

    async def update_status(self, db: AsyncSession, task_id: str, status: str) -> str:
        task = await db.get(AnalysisTask, task_id)
        if task is None:
            raise NotFoundError(f"analysis task not found: {task_id}")
        result = await db.execute(select(TeamReview).where(TeamReview.task_id == task_id))
        review = result.scalar_one_or_none()
        if review is None:
            review = TeamReview(task_id=task_id, status=status)
            db.add(review)
        else:
            review.status = status
        await db.commit()
        return review.status


def _summary(items: list[TeamQueueItemRead]) -> TeamSummaryRead:
    return TeamSummaryRead(
        total=len(items),
        high_risk=len([item for item in items if item.risk_level == "high"]),
        pending=len([item for item in items if item.status == "pending"]),
        trusted=len([item for item in items if item.risk_level == "low"]),
        medium_risk=len([item for item in items if item.risk_level == "medium"]),
        confidence_high=len([item for item in items if item.confidence == "high"]),
        confidence_medium=len([item for item in items if item.confidence == "medium"]),
        confidence_low=len([item for item in items if item.confidence == "low"]),
    )


def _related_groups(queue_rows, items: list[TeamQueueItemRead]) -> list[TeamRelatedGroupRead]:
    by_task = {item.task_id: item for item in items}
    groups: list[TeamRelatedGroupRead] = []

    account_groups: dict[str, list[TeamQueueItemRead]] = defaultdict(list)
    image_groups: dict[str, list[TeamQueueItemRead]] = defaultdict(list)
    for task, _report, sample in queue_rows:
        item = by_task.get(task.id)
        if item is None:
            continue
        handle = (sample.account_snapshot.handle if sample.account_snapshot else None) or ""
        if handle:
            account_groups[handle].append(item)
        digest = str((sample.image_meta or {}).get("sha256") or "")
        if digest:
            image_groups[digest].append(item)

    for handle, members in account_groups.items():
        if len(members) < 2:
            continue
        groups.append(
            TeamRelatedGroupRead(
                id=f"account:{handle}",
                signals=[f"同一账号 {len(members)} 篇", "账号发布轨迹可联动复核"],
                members=[_group_member(item) for item in members],
            )
        )
    for digest, members in image_groups.items():
        if len(members) < 2:
            continue
        groups.append(
            TeamRelatedGroupRead(
                id=f"image:{digest[:12]}",
                signals=["图片 SHA-256 完全相同", f"素材复用 {len(members)} 篇"],
                members=[_group_member(item) for item in members],
            )
        )
    return groups[:20]


def _group_member(item: TeamQueueItemRead) -> TeamGroupMemberRead:
    return TeamGroupMemberRead(
        id=item.id,
        task_id=item.task_id,
        title=item.title,
        author=item.author,
        created_at=item.created_at,
    )


def _sample_title(sample: ContentSample) -> str:
    text = (sample.text_content or "").strip().splitlines()
    if text and text[0].strip():
        title = text[0].strip()
        return title if len(title) <= 36 else f"{title[:36]}…"
    return sample.image_filename or f"核验任务 {sample.id[:8]}"


def _issue_type(dimension: DimensionRead | None) -> str:
    if dimension is None:
        return "无明显问题"
    evidence_types = " ".join(item.type for item in dimension.evidence).lower()
    if dimension.code == "S":
        return "绝对化功效" if any(
            token in evidence_types
            for token in ("exaggerated", "absolute", "unsupported", "claim")
        ) else "凭证矛盾"
    if dimension.code == "U":
        return "疑似刷评"
    if dimension.code == "R":
        return "疑似 AI 生成"
    return "账号异常"


def _actions(risk_level: str, issue_type: str) -> dict[str, str]:
    if risk_level == "high":
        return {
            "platform": "人工复核 · 限流观察",
            "brand": f"暂缓合作，核实{issue_type}",
        }
    if risk_level == "medium":
        return {
            "platform": "加标注 · 继续观察",
            "brand": f"索取材料，复核{issue_type}",
        }
    return {"platform": "放行", "brand": "可作合作参考"}


def _checks(dimensions: list[DimensionRead]) -> list[TeamCheckRead]:
    checks: list[TeamCheckRead] = []
    ranked = sorted(
        dimensions,
        key=lambda dimension: (
            dimension.risk_score if dimension.risk_score is not None else -1
        ),
        reverse=True,
    )
    for dimension in ranked:
        for evidence in dimension.evidence[:1]:
            checks.append(
                TeamCheckRead(
                    claim=dimension.label,
                    evidence=(evidence.reason or evidence.evidence)[:180],
                )
            )
        if len(checks) >= 2:
            break
    return checks


def _contradiction(cross_checks, decision) -> str:
    for state in ("conflict", "doubtful"):
        item = next((item for item in cross_checks if item.state == state), None)
        if item is not None:
            return item.conclusion
    if decision and decision.headline:
        return decision.headline
    return "未发现明显冲突"
