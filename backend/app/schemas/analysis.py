"""Analysis lifecycle contracts: start a run, then poll its status."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.enums import RiskLevel, TaskStatus, TrustLevel
from app.schemas.common import UTCDatetime


class AnalyzeRequest(BaseModel):
    """Optional body for ``POST /api/analyze/{task_id}``.

    Omitting it runs every available agent.
    """

    agents: list[str] | None = Field(
        default=None,
        description="Subset of agent names to run, e.g. ['text','comment']. Defaults to all.",
    )
    use_llm: bool = Field(
        default=False, description="Allow agents to augment heuristics with the configured LLM."
    )


class AnalyzeResponse(BaseModel):
    task_id: str
    status: TaskStatus
    requested_agents: list[str]
    message: str


class AgentStatusItem(BaseModel):
    """One dimension's lifecycle + verdict, as polled from ``/api/status``.

    ``code`` + ``label`` are the frontend's presentation vocabulary (T/R/U/S).
    Scores are split by direction so nothing is ambiguous:
    ``risk_score`` is 0-100 risk (higher = riskier), ``trust_score`` is 0-100
    credibility (higher = better). Both are ``None`` when the dimension ran but
    was not assessable (``assessed == false``).

    ``trust_level`` / ``trust_label`` mirror the report's so the polling view and
    the final report never disagree about how to word the same verdict.
    """

    agent: str = Field(description="Stable dimension key: account|image|comment|text")
    code: str = Field(description="Frontend dimension code: T|R|U|S")
    label: str = Field(description="Frontend dimension label, e.g. 账号可信度")
    status: TaskStatus
    assessed: bool = Field(default=False, description="Whether a real verdict exists")
    risk_level: RiskLevel | None = None
    risk_score: float | None = None
    trust_level: TrustLevel | None = None
    trust_score: float | None = None
    trust_label: str | None = Field(default=None, description="高可信 / 存疑 / 低可信")
    duration_ms: int | None = None
    error_message: str | None = None


class TaskStatusResponse(BaseModel):
    """Payload of ``GET /api/status/{task_id}`` — polling view for the UI."""

    task_id: str
    sample_id: str
    status: TaskStatus
    progress: int
    requested_agents: list[str]
    error_message: str | None = None
    created_at: UTCDatetime
    started_at: UTCDatetime | None = None
    finished_at: UTCDatetime | None = None
    report_ready: bool
    agents: list[AgentStatusItem]
