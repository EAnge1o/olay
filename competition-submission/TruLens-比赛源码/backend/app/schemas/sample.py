"""Sample retrieval contracts.

Additive to the original contract: ``POST /api/upload`` only returns ids, so
without these the frontend has no way to re-display uploaded text/comments or
fetch the analysed image (needed for the 视觉分析 panel and for reopening a
report).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import UTCDatetime


class ImageInfo(BaseModel):
    filename: str | None = Field(default=None, description="Original client filename")
    content_type: str | None = None
    size: int | None = Field(default=None, description="Bytes stored on disk")
    width: int | None = None
    height: int | None = None
    has_exif: bool | None = Field(
        default=None, description="False means metadata was stripped (AI-image signal)"
    )
    sha256: str | None = None
    url: str | None = Field(
        default=None, description="Endpoint that streams the image bytes, e.g. '/api/samples/<id>/image'"
    )


class AccountSnapshotInfo(BaseModel):
    """Account history supplied for the T (账号可信度) dimension."""

    handle: str | None = None
    profile_text: str | None = None
    follower_count: int | None = None
    total_post_count: int | None = None
    post_count: int = Field(default=0, description="Number of history posts actually stored")
    posts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Normalized posts: {id,text,published_at,like_count,comment_count,source_url}",
    )


class SampleRead(BaseModel):
    """``GET /api/samples/{sample_id}`` response."""

    id: str
    text_content: str | None
    text_length: int
    comment_count: int
    comments: list[dict[str, Any]]
    account: AccountSnapshotInfo | None = None
    image: ImageInfo | None
    source: str
    created_at: UTCDatetime


class TraceMatchRead(BaseModel):
    """One previously analysed sample whose image hash exactly matches."""

    sample_id: str
    task_id: str | None = None
    title: str
    source: str
    created_at: UTCDatetime
    trust_score: float | None = None
    risk_level: str | None = None
    image_url: str


class SampleTraceRead(BaseModel):
    """``GET /api/samples/{sample_id}/trace`` response."""

    sample_id: str
    sha256: str | None = None
    match_count: int = 0
    matches: list[TraceMatchRead] = Field(default_factory=list)
    note: str
