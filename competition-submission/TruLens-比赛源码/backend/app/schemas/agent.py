"""Agent-layer contracts.

``AgentInput`` is deliberately a plain dataclass (not Pydantic): it is an
internal in-process value object, never parsed from the wire, so validation
overhead would be pure cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import RiskLevel


@dataclass(slots=True)
class AgentInput:
    """Everything an agent may need to analyse a sample."""

    task_id: str
    text: str | None = None
    image_path: str | None = None
    image_meta: dict[str, Any] = field(default_factory=dict)
    comments: list[dict[str, Any]] = field(default_factory=list)
    # Account history for the T dimension (账号可信度).
    account_posts: list[dict[str, Any]] = field(default_factory=list)
    account_meta: dict[str, Any] = field(default_factory=dict)
    # Free-form switches, e.g. {"use_llm": True} or {"agent_results": {...}}.
    options: dict[str, Any] = field(default_factory=dict)


class Finding(BaseModel):
    """A single piece of evidence produced by an agent."""

    type: str = Field(description="Machine-readable category, e.g. 'exaggeration'")
    evidence: str = Field(description="The offending snippet or observation")
    confidence: float = Field(ge=0.0, le=1.0, description="Model/heuristic confidence")
    severity: RiskLevel = RiskLevel.MEDIUM
    location: str | None = Field(default=None, description="e.g. 'text[12:30]' or 'comment#7'")
    detail: str | None = Field(default=None, description="Human-readable explanation")


class AgentOutput(BaseModel):
    """Standard output contract for the Text / Comment / Vision agents."""

    agent: str
    risk_level: RiskLevel
    score: float = Field(ge=0.0, le=100.0, description="Risk score: higher = riskier")
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
