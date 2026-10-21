"""Request and response contracts for the dimension-routing AI agent."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from content_trust.schemas.evidence import EvidenceItem
from content_trust.schemas.input import CommentInput, TrustImageInput
from content_trust.schemas.output import (
    BatchTrustReport,
    LocalizationBox,
    TrustReport,
)

AgentDimension = Literal["image", "text", "comment", "account"]
AgentRiskLevel = Literal["low", "medium", "high"]


class AccountPostRecord(BaseModel):
    """One account-history item supplied by a caller or read from a screenshot."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str = ""
    published_at: str | None = None
    like_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    source_url: str | None = None


class AccountHistoryInput(BaseModel):
    """Account information that the analyzer is permitted to assess."""

    model_config = ConfigDict(extra="forbid")

    account_id: str | None = None
    handle: str | None = None
    profile_text: str | None = None
    follower_count: int | None = Field(default=None, ge=0)
    total_post_count: int | None = Field(default=None, ge=0)
    posts: list[AccountPostRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_post_ids(self) -> "AccountHistoryInput":
        ids = [post.id for post in self.posts]
        if len(ids) != len(set(ids)):
            raise ValueError("Account history post ids must be unique")
        return self


class AgentScreenshotInput(BaseModel):
    """A screenshot to classify and extract visible post/account material from."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    image_path: str | None = Field(default=None, min_length=1)
    image_url: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_source(self) -> "AgentScreenshotInput":
        if (self.image_path is None) == (self.image_url is None):
            raise ValueError("Provide exactly one screenshot image_path or image_url")
        return self


class AgentInput(BaseModel):
    """Unified input. Sources may be supplied directly, as screenshots, or by URL."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    content_id: str | None = None
    post_url: HttpUrl | None = None
    text: str | None = None
    images: list[TrustImageInput] = Field(default_factory=list)
    screenshots: list[AgentScreenshotInput] = Field(default_factory=list)
    comments: list[CommentInput] = Field(default_factory=list)
    account_history: AccountHistoryInput | None = None
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    comment_scope: str | None = None
    locale: str = "zh-CN"
    include_debug_log: bool = True

    @model_validator(mode="after")
    def has_input_and_unique_ids(self) -> "AgentInput":
        if not any(
            (
                self.post_url,
                self.text,
                self.images,
                self.screenshots,
                self.comments,
                self.account_history,
            )
        ):
            raise ValueError("Provide a post URL, text, image, screenshot, comments, or account history")
        for label, values in (
            ("image", [item.id for item in self.images]),
            ("screenshot", [item.id for item in self.screenshots]),
            ("comment", [item.id for item in self.comments]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label.capitalize()} ids must be unique")
        return self


class FetchedPostData(BaseModel):
    """Normalized result returned by an optional platform-specific fetch adapter."""

    model_config = ConfigDict(extra="forbid")

    content_id: str | None = None
    text: str | None = None
    images: list[TrustImageInput] = Field(default_factory=list)
    comments: list[CommentInput] = Field(default_factory=list)
    account_history: AccountHistoryInput | None = None
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    comment_scope: str | None = None


class ScreenshotTextRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["post_text", "comment", "account", "profile", "product", "other"]
    text: str = ""
    description: str = ""
    bbox: LocalizationBox | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ExtractedComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    author: str | None = None
    published_at: str | None = None
    bbox: LocalizationBox | None = None


class ScreenshotIntakeResult(BaseModel):
    """Visible screenshot extraction only; it is not a forensic verdict."""

    model_config = ConfigDict(extra="forbid")

    screen_type: Literal[
        "post", "comment_section", "account_profile", "product_photo", "other", "unknown"
    ]
    dimensions_present: list[AgentDimension] = Field(default_factory=list)
    post_text: str | None = None
    comments: list[ExtractedComment] = Field(default_factory=list)
    account_snapshot: AccountHistoryInput | None = None
    regions: list[ScreenshotTextRegion] = Field(default_factory=list)
    summary: str = ""


class AccountHistoryFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    verdict: Literal["suspicious", "insufficient_evidence", "unknown"]
    severity: Literal["low", "medium", "high"]
    risk_score: float = Field(ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    observation: str
    reason: str
    post_ids: list[str] = Field(default_factory=list)
    recommended_action: str


class AccountAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    assessed_post_count: int = Field(default=0, ge=0)
    risk_score: float | None = Field(default=None, ge=0.0, le=1.0)
    findings: list[AccountHistoryFinding] = Field(default_factory=list)
    summary: str = ""
    error: str | None = None

    @model_validator(mode="after")
    def available_requires_score(self) -> "AccountAnalysisResult":
        if self.available and self.risk_score is None:
            raise ValueError("available account analysis must include a risk_score")
        if not self.available and self.risk_score is not None:
            raise ValueError("unavailable account analysis cannot include a risk_score")
        return self


class DimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: AgentDimension
    status: Literal["assessed", "insufficient_data", "unavailable"]
    score: float | None = Field(default=None, ge=0.0, le=100.0)
    risk_level: AgentRiskLevel | None = None
    configured_weight: float = Field(ge=0.0, le=1.0)
    normalized_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    summary: str
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def score_matches_status(self) -> "DimensionAssessment":
        if self.status == "assessed" and self.score is None:
            raise ValueError("assessed dimension must include a score")
        if self.status != "assessed" and self.score is not None:
            raise ValueError("unassessed dimension cannot include a score")
        if (self.score is None) != (self.risk_level is None):
            raise ValueError("dimension score and risk_level must be set together")
        return self


class AgentRoleRun(BaseModel):
    """Visible execution status for one specialist role; no hidden reasoning."""

    model_config = ConfigDict(extra="forbid")

    role: AgentDimension
    provider: str
    model: str | None = None
    prompt_version: str | None = None
    status: Literal[
        "completed",
        "fallback",
        "partial",
        "skipped",
        "unavailable",
        "insufficient_data",
    ]
    summary: str
    error_code: str | None = None
    evidence_chain_ids: list[str] = Field(default_factory=list)


class EvidenceChainItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    dimension: AgentDimension
    source_kind: Literal["text", "image", "comment", "account", "external_evidence"]
    source_id: str | None = None
    claim_id: str | None = None
    finding_id: str | None = None
    excerpt: str | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    image_id: str | None = None
    bbox: LocalizationBox | None = None
    related_ids: list[str] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def complete_offsets(self) -> "EvidenceChainItem":
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("evidence text offsets must be provided together")
        if self.start_char is not None and self.end_char < self.start_char:
            raise ValueError("end_char must not precede start_char")
        return self


class AgentDebugEvent(BaseModel):
    """Operational trace for debugging; never contains private chain-of-thought."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    status: Literal["started", "completed", "unavailable", "failed", "skipped"]
    elapsed_ms: float | None = Field(default=None, ge=0.0)
    summary: str = ""
    dimension: AgentDimension | None = None
    error_code: str | None = None


class AgentAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    content_id: str
    input_kind: Literal["post_url", "screenshot", "image", "text", "mixed", "account_history"]
    enabled_dimensions: list[AgentDimension]
    overall_score: float | None = Field(default=None, ge=0.0, le=100.0)
    overall_risk_level: AgentRiskLevel | None = None
    major_evidence: list[str] = Field(default_factory=list)
    dimension_assessments: list[DimensionAssessment] = Field(default_factory=list)
    role_runs: list[AgentRoleRun] = Field(default_factory=list)
    evidence_chain: list[EvidenceChainItem] = Field(default_factory=list)
    summary: str
    analysis_report: TrustReport | BatchTrustReport | None = None
    account_analysis: AccountAnalysisResult | None = None
    screenshot_intakes: dict[str, ScreenshotIntakeResult] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    model_metadata: dict[str, Any] = Field(default_factory=dict)
    debug_log: list[AgentDebugEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def overall_score_matches_level(self) -> "AgentAnalysisResult":
        if (self.overall_score is None) != (self.overall_risk_level is None):
            raise ValueError("overall_score and overall_risk_level must be set together")
        return self


class FollowUpResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    answer: str
    related_dimensions: list[AgentDimension] = Field(default_factory=list)
    evidence_chain_ids: list[str] = Field(default_factory=list)
    grounded: bool

