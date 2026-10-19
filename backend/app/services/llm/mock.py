"""Mock provider — lets the whole platform run with zero credentials.

Returns deterministic text derived from the prompt so demos and tests are
reproducible. It intentionally does *not* pretend to be smart: when
``json_mode`` is requested it returns a valid, empty-ish JSON object so
callers can parse it without special-casing.
"""

from __future__ import annotations

import json
import re

from app.services.llm.base import LLMProvider, LLMResponse

_SENTENCE = re.compile(r"[。！？.!?]")


class MockLLMProvider(LLMProvider):
    name = "mock"

    @staticmethod
    def is_configured() -> bool:
        return True

    async def complete(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        if json_mode:
            payload = {
                "provider": self.name,
                "echo_digest": self._digest(prompt),
                "note": "mock provider active — configure LLM_PROVIDER for real analysis",
            }
            return LLMResponse(text=json.dumps(payload, ensure_ascii=False), model="mock-1")

        first_sentence = _SENTENCE.split(prompt.strip())[0]
        text = (
            f"[mock] 摘要：{first_sentence[:120]}"
            if first_sentence
            else "[mock] 无可分析文本"
        )
        return LLMResponse(text=text, model="mock-1")

    @staticmethod
    def _digest(prompt: str) -> str:
        return f"len={len(prompt)}"
