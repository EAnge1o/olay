"""Agent layer.

Every agent implements the same interface::

    agent: BaseAgent[TOutput]
    await agent.analyze(AgentInput) -> TOutput

The four analysis agents (text / comment / image / account) produce
:class:`AgentOutput`; the ReportAgent produces :class:`ReportContent`. Both are
the same interface with different output contracts, expressed through the
generic parameter.

Public keys equal the engine's role names and map 1:1 onto the frontend's
four-dimension report model (T/R/U/S) — see :mod:`app.core.enums`.
"""

from app.agents.account_agent import AccountAgent
from app.agents.base import BaseAgent
from app.agents.comment_agent import CommentAgent
from app.agents.image_agent import ImageAgent
from app.agents.registry import AgentRegistry, build_default_registry
from app.agents.report_agent import ReportAgent
from app.agents.text_agent import TextAgent

__all__ = [
    "AccountAgent",
    "AgentRegistry",
    "BaseAgent",
    "CommentAgent",
    "ImageAgent",
    "ReportAgent",
    "TextAgent",
    "build_default_registry",
]
