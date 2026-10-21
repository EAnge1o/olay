"""Agent registry — one place to enumerate, resolve and describe agents.

The analysis service depends on this instead of importing concrete agents, so
adding a dimension is a one-line registration change. Public keys and canonical
ordering come from :data:`app.core.enums.DIMENSION_ORDER`, which is aligned with
the frontend's T/R/U/S model.
"""

from __future__ import annotations

from typing import Any

from app.agents.account_agent import AccountAgent
from app.agents.base import BaseAgent
from app.agents.comment_agent import CommentAgent
from app.agents.image_agent import ImageAgent
from app.agents.report_agent import ReportAgent
from app.agents.text_agent import TextAgent
from app.core.enums import DIMENSION_META, DIMENSION_ORDER, AgentName
from app.core.exceptions import ValidationError
from app.services.llm.base import LLMProvider

# Dimensions that run before the report fuse, in frontend display order.
ANALYSIS_AGENT_NAMES: tuple[str, ...] = DIMENSION_ORDER


class AgentRegistry:
    def __init__(self, agents: dict[str, BaseAgent], report_agent: ReportAgent) -> None:
        self._agents = agents
        self._report_agent = report_agent

    def get(self, name: str) -> BaseAgent | None:
        return self._agents.get(name)

    @property
    def analysis_agents(self) -> dict[str, BaseAgent]:
        return dict(self._agents)

    @property
    def report_agent(self) -> ReportAgent:
        return self._report_agent

    def names(self) -> list[str]:
        """Registered dimension keys in canonical T/R/U/S order."""
        return [name for name in DIMENSION_ORDER if name in self._agents]

    def resolve_subset(self, requested: list[str] | None) -> list[str]:
        """Validate a client-requested agent list; default to all dimensions."""
        if not requested:
            return list(ANALYSIS_AGENT_NAMES)
        invalid = [n for n in requested if n not in self._agents]
        if invalid:
            raise ValidationError(
                f"unknown agent(s): {', '.join(invalid)}",
                details={"available": self.names()},
            )
        # preserve canonical order, drop duplicates
        return [n for n in ANALYSIS_AGENT_NAMES if n in set(requested)]

    def describe(self) -> list[dict[str, Any]]:
        """Agent list for ``GET /api/agents`` — includes the frontend's code/label.

        Iterates :data:`DIMENSION_ORDER` (T/R/U/S) rather than the registration
        dict, so the payload order matches how the frontend lays the rows out.
        """
        described: list[dict[str, Any]] = []
        for key in DIMENSION_ORDER:
            agent = self._agents.get(key)
            if agent is None:
                continue
            item = agent.describe()
            meta = DIMENSION_META.get(key)
            if meta is not None:
                item["code"] = meta.code
                item["label"] = meta.label
                item["question"] = meta.question
                item["input"] = meta.input_label
            described.append(item)
        return described


def build_default_registry(llm: LLMProvider | None = None) -> AgentRegistry:
    """Construct the standard four-dimension pipeline."""
    agents: dict[str, BaseAgent] = {
        AgentName.TEXT.value: TextAgent(llm=llm),
        AgentName.COMMENT.value: CommentAgent(llm=llm),
        AgentName.IMAGE.value: ImageAgent(llm=llm),
        AgentName.ACCOUNT.value: AccountAgent(llm=llm),
    }
    return AgentRegistry(agents=agents, report_agent=ReportAgent(llm=llm))
