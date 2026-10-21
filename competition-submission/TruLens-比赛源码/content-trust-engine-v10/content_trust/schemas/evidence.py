"""Evidence supplied by callers for claim verification."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    text: str
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
