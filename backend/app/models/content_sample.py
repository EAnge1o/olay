"""ContentSample — one uploaded piece of creator content.

A single row captures everything the creator submitted in one request:
the copy (``text_content``), the image (path + probed metadata), and the
comment payload. Analyses reference a sample via ``AnalysisTask``, so the
same content can be re-analysed many times as models improve.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ContentSample(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_samples"

    # -- Copy (种草文案) ---------------------------------------------------
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # -- Comments ----------------------------------------------------------
    # Normalized to [{"index", "content", "author", "likes", "created_at"}].
    # Stored as JSON: the comment shape is loose and agent-owned, and we never
    # need to query individual comments in SQL.
    comment_payload: Mapped[list | None] = mapped_column(JSON, nullable=True)
    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # -- Image -------------------------------------------------------------
    image_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # {"content_type","size","sha256","width","height","has_exif",...}
    image_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # -- Provenance --------------------------------------------------------
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    raw_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # -- Account history (T 维度) ------------------------------------------
    # 1:1 companion row; kept in its own table so no column migration is needed.
    account_snapshot: Mapped["AccountSnapshot | None"] = relationship(
        back_populates="sample",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    tasks: Mapped[list["AnalysisTask"]] = relationship(
        back_populates="sample",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ContentSample id={self.id} images={bool(self.image_path)} comments={self.comment_count}>"
