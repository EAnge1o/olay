"""Reusable declarative mixins.

Primary keys are opaque 32-char hex UUID strings rather than DB-native UUIDs:
this keeps a single schema definition portable across SQLite (dev) and
PostgreSQL (prod) with zero dialect branches.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_id, utcnow


class UUIDPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id, nullable=False
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
