"""Creator pre-publish self-check contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.report import ReportRead


class CreatorIssueRead(BaseModel):
    id: str
    severity: Literal["must", "suggest", "tip"]
    type: str
    original: str | None = None
    suggestion: str
    reason: str
    remove: bool = False


class CreatorCheckRead(BaseModel):
    task_id: str
    sample_id: str
    publication_state: Literal["ready", "revise", "hold"]
    publication_title: str
    copy_score: float | None = None
    image_score: float | None = None
    issues: list[CreatorIssueRead] = Field(default_factory=list)
    disclosures: dict[str, bool] = Field(default_factory=dict)
    good_points: list[str] = Field(default_factory=list)
    check_date: str
    report: ReportRead
