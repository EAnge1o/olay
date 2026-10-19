"""Team verification queue contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UTCDatetime


class TeamCheckRead(BaseModel):
    claim: str
    evidence: str


class TeamQueueItemRead(BaseModel):
    id: str = Field(description="Stable queue id; currently the task id")
    task_id: str
    sample_id: str
    title: str
    author: str
    score: float = Field(description="Credibility score, higher is better")
    risk_level: Literal["low", "medium", "high"]
    confidence: Literal["high", "medium", "low"]
    dimensions: list[str]
    contradiction: str
    issue_type: str
    actions: dict[str, str]
    status: Literal["pending", "watching", "done"]
    checks: list[TeamCheckRead] = Field(default_factory=list)
    created_at: UTCDatetime


class TeamSummaryRead(BaseModel):
    total: int
    high_risk: int
    pending: int
    trusted: int
    medium_risk: int
    confidence_high: int
    confidence_medium: int
    confidence_low: int


class TeamGroupMemberRead(BaseModel):
    id: str
    task_id: str
    title: str
    author: str
    created_at: UTCDatetime


class TeamRelatedGroupRead(BaseModel):
    id: str
    signals: list[str]
    members: list[TeamGroupMemberRead]


class TeamQueueRead(BaseModel):
    items: list[TeamQueueItemRead]
    summary: TeamSummaryRead
    groups: list[TeamRelatedGroupRead] = Field(default_factory=list)
    generated_at: UTCDatetime


class TeamReviewUpdate(BaseModel):
    status: Literal["pending", "watching", "done"]
