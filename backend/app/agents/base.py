"""The single interface every analysis agent must satisfy.

Design notes
------------
* **Generic output.** ``BaseAgent[AgentOutput]`` vs ``BaseAgent[ReportContent]``
  keeps one call signature while letting the fuse stage return a richer type.
* **LLM is optional and injected.** Agents run deterministic rule-based
  heuristics by default (so the product is always demoable and testable), and
  *augment* with a real model only when ``options["use_llm"]`` is set. The
  model is never hardcoded — it arrives as :class:`LLMProvider`.
* **No I/O beyond the payload.** Agents never touch the DB or the filesystem
  session; the service layer owns persistence. This makes agents trivially
  unit-testable and safe to run concurrently.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from app.core.utils import safe_json_loads
from app.schemas.agent import AgentInput
from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)

TOutput = TypeVar("TOutput")


class BaseAgent(ABC, Generic[TOutput]):
    """Abstract base for all agents."""

    name: ClassVar[str] = "base"
    display_name: ClassVar[str] = "Base Agent"
    description: ClassVar[str] = ""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm

    # -- Contract ----------------------------------------------------------
    @abstractmethod
    async def analyze(self, payload: AgentInput) -> TOutput:
        """Analyse the sample and return this agent's output contract."""
        raise NotImplementedError

    # -- Shared helpers ----------------------------------------------------
    @staticmethod
    def wants_llm(payload: AgentInput) -> bool:
        return bool(payload.options.get("use_llm"))

    async def call_llm_json(
        self, *, system: str, prompt: str, max_tokens: int = 1200
    ) -> dict[str, Any] | None:
        """Ask the injected provider for a JSON object.

        Returns ``None`` (never raises) when the model is unavailable or returns
        unusable output — heuristics remain the source of truth either way.
        """
        if self.llm is None:
            return None
        try:
            response = await self.llm.complete(
                system=system, prompt=prompt, json_mode=True, max_tokens=max_tokens
            )
        except Exception:  # noqa: BLE001 - augmentation must never break analysis
            logger.warning("[%s] LLM augmentation failed; using heuristics only", self.name)
            return None

        parsed = safe_json_loads(response.text)
        if isinstance(parsed, dict):
            return parsed
        logger.debug("[%s] LLM returned non-JSON payload", self.name)
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "llm_enabled": self.llm is not None,
        }


def dump_findings(findings: list) -> list[dict[str, Any]]:
    """JSON-serializable view of findings for persistence."""
    return [json.loads(f.model_dump_json()) for f in findings]
