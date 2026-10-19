"""Upload endpoint contract."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.enums import TaskStatus


class UploadResponse(BaseModel):
    """Returned immediately after ingest — the pipeline has not run yet."""

    task_id: str
    sample_id: str
    status: TaskStatus
    comment_count: int
    text_length: int
    has_image: bool
    # T (账号可信度) dimension: how many history posts the caller supplied.
    account_post_count: int = 0
    # Set when the caller submitted a Xiaohongshu link; the browser crawl runs
    # asynchronously, so this echoes the canonical URL that will be fetched.
    source_url: str | None = None
