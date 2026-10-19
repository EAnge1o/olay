"""Report — the final fused credibility verdict for a task (1:1 with the task)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Report(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reports"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    overall_score: Mapped[float] = mapped_column(Float, nullable=False)  # risk 0-100
    trust_score: Mapped[float] = mapped_column(Float, nullable=False)    # 100 - risk
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recommendations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dimension_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["AnalysisTask"] = relationship(back_populates="report")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Report task={self.task_id} risk={self.risk_level} score={self.overall_score}>"
