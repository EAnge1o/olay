"""Base schema config, the shared error envelope, and the datetime wire type."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer


def _iso_utc(value: datetime) -> str:
    """Serialize a datetime as unambiguous, timezone-aware ISO 8601 (UTC)."""
    if value.tzinfo is None:
        # SQLite has no timezone support, so timestamps round-trip as naive
        # UTC. Re-attach UTC so clients in any timezone parse them correctly.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


# Wire format for every timestamp we return: "2026-10-13T14:16:10.939918+00:00".
# Without this, browsers interpret the bare string as *local* time.
UTCDatetime = Annotated[
    datetime, PlainSerializer(_iso_utc, return_type=str, when_used="always")
]


class ORMModel(BaseModel):
    """Base for schemas populated from SQLAlchemy objects."""

    model_config = ConfigDict(from_attributes=True)


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    error: ApiError
