"""AgentResult — the persisted output of a single agent for a single task.

One row per (task, agent). ``findings`` / ``recommendations`` / ``metrics``
are JSON because their shape is owned by each agent and evolves independently.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.enums import TaskStatus
from app.database.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class AgentResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_results"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.PENDING.value, nullable=False
    )

    # Risk-oriented score: 0 = clean, 100 = maximum risk.
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    findings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recommendations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    task: Mapped["AnalysisTask"] = relationship(back_populates="agent_results")

    @property
    def assessed(self) -> bool:
        """True when the dimension produced a real verdict.

        Both backends stamp ``engine_status`` into ``raw_output``: ``assessed``
        means a verdict exists; ``not_applicable`` / ``insufficient_data`` /
        ``unavailable`` mean the dimension ran but had nothing to judge — and so
        must be shown as "暂不评分" rather than as a misleading 0.
        """
        if self.status != TaskStatus.COMPLETED.value:
            return False
        return str((self.raw_output or {}).get("engine_status", "assessed")) == "assessed"

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<AgentResult task={self.task_id} agent={self.agent_name} status={self.status} score={self.score}>"
