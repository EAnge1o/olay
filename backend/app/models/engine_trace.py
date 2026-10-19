"""EngineTrace — the raw, high-fidelity output of the Applied-AI trust engine.

Kept in its own table (rather than new columns on ``reports``) for two reasons:

* ``Base.metadata.create_all`` creates missing *tables* but never adds columns,
  so a new table is the only additive schema change that is safe for developers
  who already have a ``storage/trulens.db``.
* The trace is bulky and optional — the report stays lean for the common path.

One row per task, only present when the engine actually produced the analysis.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class EngineTrace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "engine_traces"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    backend: Mapped[str] = mapped_column(String(40), default="trust_engine", nullable=False)
    engine_schema_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Full structured payloads, stored verbatim from the engine:
    #   visible_roles  -> four-role execution status (provider/model/prompt/status)
    #   evidence_chain -> every claim / observation / comment with char offsets
    #   limitations    -> the engine's explicit caveats
    visible_roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_chain: Mapped[list | None] = mapped_column(JSON, nullable=True)
    major_evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    limitations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dimension_assessments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    provider_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    task: Mapped["AnalysisTask"] = relationship(back_populates="engine_trace")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<EngineTrace task={self.task_id} score={self.overall_score} evidence={self.evidence_count}>"
