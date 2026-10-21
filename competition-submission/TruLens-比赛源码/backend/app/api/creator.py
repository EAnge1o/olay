"""Synchronous creator pre-publish check backed by the real analysis agents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_analysis_service,
    get_db,
    get_file_service,
    get_report_service,
)
from app.core.enums import TaskStatus
from app.core.exceptions import ValidationError
from app.models.content_sample import ContentSample
from app.schemas.creator import CreatorCheckRead
from app.services.analysis_service import AnalysisService
from app.services.creator_service import build_creator_check
from app.services.file_service import FileService
from app.services.report_intelligence import build_report_read
from app.services.report_service import ReportService

router = APIRouter(prefix="/creator", tags=["creator"])


@router.post(
    "/check",
    response_model=CreatorCheckRead,
    summary="运行创作者发布前真实自检",
)
async def run_creator_check(
    draft: str = Form(default=""),
    is_collaboration: bool = Form(default=False),
    category: str = Form(default="护肤"),
    image: UploadFile | None = File(default=None, description="可选：首张待发布配图"),
    db: AsyncSession = Depends(get_db),
    analysis_service: AnalysisService = Depends(get_analysis_service),
    file_service: FileService = Depends(get_file_service),
    report_service: ReportService = Depends(get_report_service),
) -> CreatorCheckRead:
    clean_draft = draft.strip()
    has_image = bool(image is not None and image.filename)
    if not clean_draft and not has_image:
        raise ValidationError("creator check requires draft text or an image")

    image_path: str | None = None
    image_filename: str | None = None
    image_meta: dict | None = None
    if has_image:
        stored = await file_service.save_image(image)
        image_path = stored["path"]
        image_filename = stored["filename"]
        image_meta = stored["meta"]

    sample = ContentSample(
        text_content=clean_draft or None,
        text_length=len(clean_draft),
        image_path=image_path,
        image_filename=image_filename,
        image_meta=image_meta,
        source="creator-selfcheck",
        raw_metadata={
            "category": category,
            "is_collaboration": is_collaboration,
        },
    )
    db.add(sample)
    await db.flush()

    agents = ["text"]
    if has_image:
        agents.insert(0, "image")
    task = await analysis_service.create_task(
        db,
        sample,
        requested_agents=agents,
        use_llm=False,
    )
    await db.commit()

    await analysis_service.run_task(task.id)
    await db.refresh(task)
    if task.status == TaskStatus.FAILED.value:
        raise ValidationError(task.error_message or "creator check failed")

    report = await report_service.get_report(db, task.id)
    rows = await report_service.get_agent_results(db, task.id)
    report_read = build_report_read(report, rows)
    result = build_creator_check(
        draft=clean_draft,
        report=report_read,
        is_collaboration=is_collaboration,
    )
    return result.model_copy(update={"sample_id": sample.id})
