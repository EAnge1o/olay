"""AccountSnapshot — account history for the T (账号可信度) dimension.

Stored in its own table rather than as new columns on ``content_samples``:
``Base.metadata.create_all`` creates missing *tables* but never adds columns, so
a new table is the only additive change that is safe for anyone who already has
a ``storage/*.db`` file.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class AccountSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "account_snapshots"

    sample_id: Mapped[str] = mapped_column(
        ForeignKey("content_samples.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_post_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Normalized list of {id, text, published_at, like_count, comment_count, source_url}
    posts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    post_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    sample: Mapped["ContentSample"] = relationship(back_populates="account_snapshot")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<AccountSnapshot sample={self.sample_id} posts={self.post_count}>"
