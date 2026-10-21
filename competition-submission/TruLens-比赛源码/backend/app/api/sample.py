"""GET /api/samples/{sample_id} and /api/samples/{sample_id}/image.

Read-only endpoints that give the frontend access to what was uploaded —
including the image bytes, which ``POST /api/upload`` cannot return as JSON.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.content_sample import ContentSample
from app.schemas.sample import (
    AccountSnapshotInfo,
    ImageInfo,
    SampleRead,
    SampleTraceRead,
    TraceMatchRead,
)

router = APIRouter(tags=["sample"])


@router.get("/samples/{sample_id}", response_model=SampleRead, summary="获取上传内容详情")
async def get_sample(sample_id: str, db: AsyncSession = Depends(get_db)) -> SampleRead:
    sample = await db.get(ContentSample, sample_id)
    if sample is None:
        raise NotFoundError(f"content sample not found: {sample_id}")

    meta = sample.image_meta or {}
    image: ImageInfo | None = None
    if sample.image_path:
        image = ImageInfo(
            filename=sample.image_filename,
            content_type=meta.get("content_type"),
            size=meta.get("size"),
            width=meta.get("width"),
            height=meta.get("height"),
            has_exif=meta.get("has_exif"),
            sha256=meta.get("sha256"),
            url=f"{get_settings().api_prefix}/samples/{sample.id}/image",
        )

    snapshot = sample.account_snapshot
    account: AccountSnapshotInfo | None = None
    if snapshot is not None:
        account = AccountSnapshotInfo(
            handle=snapshot.handle,
            profile_text=snapshot.profile_text,
            follower_count=snapshot.follower_count,
            total_post_count=snapshot.total_post_count,
            post_count=snapshot.post_count,
            posts=snapshot.posts or [],
        )

    return SampleRead(
        id=sample.id,
        text_content=sample.text_content,
        text_length=sample.text_length,
        comment_count=sample.comment_count,
        comments=sample.comment_payload or [],
        account=account,
        image=image,
        source=sample.source,
        created_at=sample.created_at,
    )


@router.get("/samples/{sample_id}/image", summary="获取上传的图片原文件")
async def get_sample_image(sample_id: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    sample = await db.get(ContentSample, sample_id)
    if sample is None:
        raise NotFoundError(f"content sample not found: {sample_id}")
    if not sample.image_path:
        raise NotFoundError("sample has no image", details={"sample_id": sample_id})

    path = Path(sample.image_path)
    if not path.is_file():
        raise NotFoundError("image file missing on disk", details={"sample_id": sample_id})

    return FileResponse(
        path,
        # "inline" so <img src="..."> renders it instead of forcing a download.
        content_disposition_type="inline",
        media_type=(sample.image_meta or {}).get("content_type") or "application/octet-stream",
        filename=sample.image_filename or path.name,
    )


@router.get(
    "/samples/{sample_id}/trace",
    response_model=SampleTraceRead,
    summary="按图片 SHA-256 在已核验内容库中查重",
)
async def get_sample_trace(
    sample_id: str,
    db: AsyncSession = Depends(get_db),
) -> SampleTraceRead:
    sample = await db.get(ContentSample, sample_id)
    if sample is None:
        raise NotFoundError(f"content sample not found: {sample_id}")

    digest = str((sample.image_meta or {}).get("sha256") or "").strip() or None
    if not digest:
        return SampleTraceRead(
            sample_id=sample.id,
            note="本次样本没有可溯源的图片哈希。",
        )

    result = await db.execute(
        select(ContentSample)
        .where(ContentSample.id != sample.id)
        .order_by(ContentSample.created_at.desc())
        .limit(500)
    )
    matches: list[TraceMatchRead] = []
    for candidate in result.scalars().all():
        if str((candidate.image_meta or {}).get("sha256") or "") != digest:
            continue
        completed_task = next(
            (
                task
                for task in candidate.tasks
                if task.report is not None
            ),
            None,
        )
        report = completed_task.report if completed_task else None
        matches.append(
            TraceMatchRead(
                sample_id=candidate.id,
                task_id=completed_task.id if completed_task else None,
                title=_sample_title(candidate),
                source=candidate.source,
                created_at=candidate.created_at,
                trust_score=report.trust_score if report else None,
                risk_level=report.risk_level if report else None,
                image_url=f"{get_settings().api_prefix}/samples/{candidate.id}/image",
            )
        )

    return SampleTraceRead(
        sample_id=sample.id,
        sha256=digest,
        match_count=len(matches),
        matches=matches,
        note=(
            f"在已核验内容库中发现 {len(matches)} 个完全相同的图片哈希。"
            if matches
            else "未在已核验内容库中发现完全相同的图片哈希。"
        ),
    )


def _sample_title(sample: ContentSample) -> str:
    text = (sample.text_content or "").strip().splitlines()
    if text and text[0].strip():
        return text[0].strip()[:60]
    return sample.image_filename or f"样本 {sample.id[:8]}"
