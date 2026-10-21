"""Provider selection — the only place a concrete LLM is instantiated."""

from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.services.llm.base import LLMProvider
from app.services.llm.mock import MockLLMProvider
from app.services.llm.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()

    provider = (settings.llm_provider or "mock").lower()
    if provider in {"openai", "openai-compatible", "azure"}:
        candidate = OpenAICompatibleProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        if candidate.is_configured():
            logger.info("LLM provider: %s (model=%s)", candidate.name, settings.llm_model)
            return candidate
        logger.warning("LLM_PROVIDER=%s but no API key set — falling back to mock", provider)

    return MockLLMProvider()
