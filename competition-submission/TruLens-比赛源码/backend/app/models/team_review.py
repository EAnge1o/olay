"""Persistent human-review state for the team verification queue."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class TeamReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_reviews"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True, nullable=False
    )

    task: Mapped["AnalysisTask"] = relationship()
