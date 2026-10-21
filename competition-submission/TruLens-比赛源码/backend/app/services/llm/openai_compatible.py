"""OpenAI-compatible provider.

Works with OpenAI, Azure OpenAI, DeepSeek, Moonshot, Qwen (DashScope compatible
mode), together.ai, local vLLM/Ollama — anything exposing
``POST {base_url}/chat/completions``.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx

from app.core.exceptions import ExternalServiceError
from app.services.llm.base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    name: ClassVar[str] = "openai"

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        timeout_seconds: int = 30,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self._api_key:
            raise ExternalServiceError("LLM api key is not configured")

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions", json=body, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:  # network, timeout, 4xx/5xx
            logger.warning("LLM request failed: %s", exc)
            raise ExternalServiceError(f"LLM request failed: {exc}") from exc

        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceError("malformed LLM response") from exc

        return LLMResponse(
            text=text,
            model=data.get("model", self._model),
            usage=data.get("usage", {}),
            raw=data,
        )
