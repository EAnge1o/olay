"""POST /api/upload — ingest content and open an analysis task."""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_analysis_service, get_db, get_file_service, get_xhs_service
from app.core.exceptions import ValidationError
from app.core.utils import normalize_account_history, normalize_comments, safe_json_loads
from app.models.account_snapshot import AccountSnapshot
from app.models.content_sample import ContentSample
from app.schemas.upload import UploadResponse
from app.services.analysis_service import AnalysisService
from app.services.file_service import FileService
from app.services.xhs_service import XhsError, XhsService

router = APIRouter(tags=["ingest"])


def _parse_agents(raw: str | None) -> list[str] | None:
    """Accept 'text,comment' or '["text","comment"]'."""
    if not raw:
        return None
    parsed = safe_json_loads(raw)
    if isinstance(parsed, list):
        return [str(x) for x in parsed]
    return [part.strip() for part in raw.split(",") if part.strip()]


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=201,
    summary="上传种草文案 / 图片 / 评论，创建分析任务",
)
async def upload_content(
    request: Request,
    background: BackgroundTasks,
    text: str | None = Form(default=None, description="美妆种草文案"),
    comments: str | None = Form(default=None, description="评论：JSON 数组或逐行文本"),
    account_history: str | None = Form(
        default=None,
        description="账号历史（T 维度）：JSON，形如 {\"handle\":..,\"posts\":[{text,published_at,..}]}",
    ),
    url: str | None = Form(
        default=None,
        description="小红书笔记链接或分享文案；抓取在后台异步执行（见 XHS_ENABLED）",
    ),
    account_url: str | None = Form(
        default=None,
        description="可选的小红书账号主页链接；留空时尝试从笔记作者自动获取",
    ),
    image: UploadFile | None = File(default=None, description="图片素材"),
    source: str = Form(default="manual", description="来源标识"),
    agents: str | None = Form(default=None, description="要运行的 Agent 子集"),
    use_llm: bool = Form(default=False, description="是否启用模型复核"),
    auto_analyze: bool = Form(default=True, description="上传后立即启动分析"),
    db: AsyncSession = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    xhs_service: XhsService = Depends(get_xhs_service),
    analysis_service: AnalysisService = Depends(get_analysis_service),
) -> UploadResponse:
    clean_text = (text or "").strip()
    normalized_comments = normalize_comments(comments)
    normalized_account = normalize_account_history(account_history)
    has_image = bool(image is not None and image.filename)

    # Resolve the link eagerly (no browser): a bad paste is a 422 here instead of
    # a doomed background task. The crawl itself happens in the pipeline.
    canonical_url: str | None = None
    if url and url.strip():
        if not xhs_service.available:
            raise ValidationError(f"无法抓取该链接：{xhs_service.unavailable_reason()}")
        try:
            canonical_url = xhs_service.normalize(url)
        except XhsError as exc:
            raise ValidationError(f"无法识别的链接：{exc.reason}") from exc

    canonical_account_url: str | None = None
    if account_url and account_url.strip():
        if not canonical_url:
            raise ValidationError("账号主页链接需要与笔记链接一起使用")
        try:
            canonical_account_url = xhs_service.normalize_account(account_url)
        except XhsError as exc:
            raise ValidationError(f"无法识别的账号主页链接：{exc.reason}") from exc

    # The author homepage is optional: the crawl runner discovers the author's
    # profile URL from the note page itself (`account_hint or _profile_url(...)`
    # in scripts/xhs_fetch.py) and crawls it for the T dimension, so a note link
    # alone already yields all four dimensions. Supplying it explicitly only
    # overrides that discovery, which is still useful when the note page does not
    # expose a stable user id.

    # Validate on *meaningful* content: a whitespace-only copy or an empty
    # comment array would otherwise create a task with nothing to analyse.
    if not (clean_text or normalized_comments or normalized_account or canonical_url or has_image):
        raise ValidationError(
            "at least one of text / comments / account_history / url / image is required"
        )

    image_path: str | None = None
    image_filename: str | None = None
    image_meta: dict | None = None
    if has_image:
        stored = await file_service.save_image(image)
        image_path = stored["path"]
        image_filename = stored["filename"]
        image_meta = stored["meta"]

    sample = ContentSample(
        text_content=clean_text or None,
        text_length=len(clean_text),
        comment_payload=normalized_comments or None,
        comment_count=len(normalized_comments),
        image_path=image_path,
        image_filename=image_filename,
        image_meta=image_meta,
        source="xiaohongshu" if canonical_url else source,
        raw_metadata={
            "client": request.client.host if request.client else None,
            # The pipeline reads this to know it must crawl before analysing.
            "source_url": canonical_url,
            # Optional fallback when automatic author-profile discovery fails.
            "account_url": canonical_account_url,
        },
    )
    db.add(sample)
    await db.flush()

    if normalized_account is not None:
        db.add(
            AccountSnapshot(
                sample_id=sample.id,
                handle=normalized_account["handle"],
                profile_text=normalized_account["profile_text"],
                follower_count=normalized_account["follower_count"] or None,
                total_post_count=normalized_account["total_post_count"] or None,
                posts=normalized_account["posts"],
                post_count=len(normalized_account["posts"]),
            )
        )

    task = await analysis_service.create_task(
        db,
        sample,
        requested_agents=_parse_agents(agents),
        use_llm=use_llm,
    )
    await db.commit()

    if auto_analyze:
        background.add_task(analysis_service.run_task, task.id)

    return UploadResponse(
        task_id=task.id,
        sample_id=sample.id,
        status=task.status,
        comment_count=sample.comment_count,
        text_length=sample.text_length,
        has_image=bool(image_path),
        account_post_count=len(normalized_account["posts"]) if normalized_account else 0,
        source_url=canonical_url,
    )
