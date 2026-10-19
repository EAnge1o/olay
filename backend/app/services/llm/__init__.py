"""LLM provider abstraction.

Nothing in the codebase constructs a concrete model client directly. Agents
depend on :class:`LLMProvider`; which implementation is injected is decided
once, from settings, in :func:`get_llm_provider`. Swapping OpenAI for DeepSeek,
Qwen, Azure or a self-hosted vLLM is a config change, not a code change.
"""

from app.services.llm.base import LLMProvider, LLMResponse
from app.services.llm.factory import get_llm_provider

__all__ = ["LLMProvider", "LLMResponse", "get_llm_provider"]
