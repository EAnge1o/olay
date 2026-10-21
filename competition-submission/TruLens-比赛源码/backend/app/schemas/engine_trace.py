"""Read contract for the trust engine's execution trace.

Additive: the engine's own outputs (four-role execution, evidence chain,
limitations) are richer than our frozen report shape. They are stored in
``engine_traces`` and served here.

Sub-models use ``extra="ignore"`` on purpose: the engine is owned by another
team and may add fields between deliveries, which must not break our endpoint.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RiskLevel
from app.schemas.common import ORMModel, UTCDatetime

_LOOSE = ConfigDict(extra="ignore")


class EngineRoleRun(BaseModel):
    """One specialist role's visible execution status (no hidden reasoning)."""

    model_config = _LOOSE

    role: str = Field(description="text | comment | account | image")
    provider: str = ""
    model: str | None = None
    prompt_version: str | None = None
    status: str = Field(
        default="unknown",
        description="completed | fallback | partial | skipped | unavailable | insufficient_data",
    )
    summary: str = ""
    error_code: str | None = None
    evidence_chain_ids: list[str] = Field(default_factory=list)


class EngineEvidenceItem(BaseModel):
    """One traceable item: a claim, image observation, comment or external source."""

    model_config = _LOOSE

    id: str
    dimension: str = "unknown"
    source_kind: str = "unknown"
    source_id: str | None = None
    excerpt: str | None = None
    explanation: str = ""
    start_char: int | None = None
    end_char: int | None = None
    image_id: str | None = None
    claim_id: str | None = None
    finding_id: str | None = None
    related_ids: list[str] = Field(default_factory=list)
    bbox: dict[str, Any] | None = None


class EngineDimensionAssessment(BaseModel):
    """Per-dimension score/level plus the engine's evidence and caveats."""

    model_config = _LOOSE

    dimension: str
    status: str = Field(default="unavailable", description="assessed | insufficient_data | unavailable")
    score: float | None = None
    risk_level: RiskLevel | None = None
    configured_weight: float | None = None
    normalized_weight: float | None = None
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EngineTraceRead(ORMModel):
    """``GET /api/engine-trace/{task_id}`` response."""

    id: str
    task_id: str
    backend: str
    engine_schema_version: str | None = None

    overall_score: float | None = None
    overall_risk_level: RiskLevel | None = None

    visible_roles: list[EngineRoleRun] = Field(default_factory=list)
    evidence_chain: list[EngineEvidenceItem] = Field(default_factory=list)
    major_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    dimension_assessments: list[EngineDimensionAssessment] = Field(default_factory=list)
    provider_errors: list[str] = Field(default_factory=list)
    model_metadata: dict[str, Any] = Field(default_factory=dict)

    evidence_count: int = 0
    latency_ms: int | None = None
    created_at: UTCDatetime
