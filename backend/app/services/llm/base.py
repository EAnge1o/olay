"""The provider contract every model integration must satisfy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class LLMResponse(BaseModel):
    text: str
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class LLMProvider(ABC):
    """Minimal text-completion interface.

    Deliberately narrow: agents only need "give me a prompt, get text back".
    Structured output is handled by asking for JSON and parsing in the caller,
    which keeps this interface portable across very different vendors.
    """

    name: ClassVar[str] = "base"

    @staticmethod
    @abstractmethod
    def is_configured() -> bool:
        """Whether this provider can actually serve requests."""

    @abstractmethod
    async def complete(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Single-turn completion."""
