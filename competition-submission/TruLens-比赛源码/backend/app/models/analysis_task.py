"""AnalysisTask — one execution of the multi-agent pipeline over a sample."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.enums import TaskStatus
from app.database.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class AnalysisTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_tasks"

    sample_id: Mapped[str] = mapped_column(
        ForeignKey("content_samples.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.PENDING.value, index=True, nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Which dimensions the client asked for, e.g. ["account", "image", "comment", "text"].
    requested_agents: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Per-run switches captured at request time, e.g. {"use_llm": true}.
    run_options: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sample: Mapped["ContentSample"] = relationship(back_populates="tasks")
    agent_results: Mapped[list["AgentResult"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AgentResult.created_at",
    )
    report: Mapped["Report | None"] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    engine_trace: Mapped["EngineTrace | None"] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<AnalysisTask id={self.id} status={self.status} progress={self.progress}>"
