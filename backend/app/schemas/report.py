"""Report contracts.

``ReportContent`` is the ReportAgent's output model (pure, no DB).
``ReportRead`` is the API view of the persisted ``Report`` row.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import RiskLevel, TrustLevel
from app.schemas.common import ORMModel, UTCDatetime


class EvidenceItem(BaseModel):
    """A finding selected for the final report, tagged with its origin."""

    source: str = Field(description="Dimension key that produced it: account|image|comment|text")
    type: str
    evidence: str
    confidence: float = Field(ge=0.0, le=1.0)
    severity: RiskLevel
    reason: str | None = None


class ReportContent(BaseModel):
    """Structured credibility verdict produced by the ReportAgent."""

    overall_score: float = Field(ge=0.0, le=100.0, description="Risk score, higher = riskier")
    trust_score: float = Field(ge=0.0, le=100.0, description="100 - overall_score, for display")
    risk_level: RiskLevel
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    assessed_dimensions: list[str] = Field(
        default_factory=list,
        description="Keys of the dimensions that produced a real verdict; empty => nothing was assessable",
    )


class DimensionRead(BaseModel):
    """One of the four dimensions, in the frontend's T/R/U/S vocabulary.

    Everything the report page needs to draw one row (badge, bar width, score,
    evidence drill-down) lives here, so the frontend never has to re-derive it
    from ``dimension_scores``.

    ``trust_score`` is the credibility score the frontend renders (0-100, higher
    = more trustworthy) — it is ``None`` when ``assessed`` is false, which the UI
    shows as "暂不评分" instead of a misleading 0. ``risk_score`` /
    ``risk_level`` preserve the internal risk orientation for audits.

    The names mirror the report level on purpose: ``overall_score`` +
    ``trust_score`` + ``risk_level`` + ``trust_level`` there, ``risk_score`` +
    ``trust_score`` + ``risk_level`` + ``trust_level`` here. A bare ``score``
    would be ambiguous about which direction is "good".
    """

    key: str = Field(description="Stable dimension key: account|image|comment|text")
    code: str = Field(description="T|R|U|S")
    label: str = Field(description="e.g. 账号可信度")
    question: str = Field(description="The user-facing question this dimension answers")
    input_label: str = Field(description="What the caller should upload, e.g. 账号主页")
    assessed: bool = Field(description="False -> render the '暂不评分' state")
    trust_score: float | None = Field(
        default=None, ge=0.0, le=100.0, description="Credibility 0-100, None when not assessed"
    )
    risk_score: float | None = Field(default=None, ge=0.0, le=100.0)
    risk_level: RiskLevel | None = None
    trust_level: TrustLevel | None = None
    trust_label: str | None = Field(default=None, description="高可信 / 存疑 / 低可信")
    summary: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CrossCheckRead(BaseModel):
    """One server-computed cross-dimension relation."""

    id: str
    from_code: str = Field(description="Source dimension code: T|R|U|S")
    to_code: str = Field(description="Target dimension code: T|R|U|S")
    left: str
    right: str
    conclusion: str
    state: Literal["conflict", "consistent", "corroborated", "doubtful", "unverified"]
    dimension: str
    detail: str
    confidence: Literal["高", "中", "低"] | None = None
    method: Literal["llm", "rule"] = "rule"
    label: str | None = None


class DecisionAdviceRead(BaseModel):
    """One recommendation tailored to a decision-maker perspective."""

    perspective: Literal["consumer", "creator", "platform", "brand"]
    priority: Literal["重点", "建议"] = "建议"
    title: str
    basis: str
    action: str | None = None


class DecisionRead(BaseModel):
    """Backend-owned decision tier and role-specific advice."""

    tier: Literal["pass", "verify", "review"]
    label: str
    headline: str
    advice: list[DecisionAdviceRead] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReportConfidenceRead(BaseModel):
    """Confidence summary derived from real assessed dimensions and evidence."""

    level: Literal["high", "medium", "low"]
    label: Literal["高", "中", "低"]
    bars: int = Field(ge=1, le=3)
    assessed_count: int = Field(ge=0, le=4)
    evidence_count: int = Field(ge=0)
    cross_check_count: int = Field(ge=0)
    missing_materials: list[str] = Field(default_factory=list)


class ReportRead(ORMModel):
    """``GET /api/report/{task_id}`` response."""

    id: str
    task_id: str
    overall_score: float = Field(description="Risk score 0-100, higher = riskier")
    trust_score: float = Field(description="Credibility 0-100, higher = better")
    risk_level: RiskLevel
    trust_level: TrustLevel
    trust_label: str
    summary: str
    evidence: list[EvidenceItem]
    recommendations: list[str]
    dimension_scores: dict[str, float] = Field(
        default_factory=dict, description="Risk score per dimension key (kept for audits)"
    )
    dimensions: list[DimensionRead] = Field(
        default_factory=list, description="Full four-dimension view, in T/R/U/S order"
    )
    cross_checks: list[CrossCheckRead] = Field(
        default_factory=list, description="Server-computed cross-dimension relations"
    )
    decision: DecisionRead | None = Field(
        default=None, description="Backend-owned tier and role-specific decision advice"
    )
    confidence: ReportConfidenceRead | None = Field(
        default=None, description="Confidence derived from the actual report payload"
    )
    assessed_dimensions: list[str] = Field(
        default_factory=list,
        description="Keys that produced a real verdict; empty => show '暂不评分' overall",
    )
    generated_at: UTCDatetime | None = None
    created_at: UTCDatetime
