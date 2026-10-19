"""Zhipu GLM vision adapter — replaces the engine's Qwen-VL image role.

Why this exists
---------------
The engine ships exactly one vision provider (``QwenVisionClient`` → DashScope).
This project uses Zhipu GLM instead. Rather than editing the engine's package
(owned by the Applied-AI teammate and due to be re-delivered), we subclass its
client and inject it through the engine's own extension point,
``ContentTrustEngine(crossmodal_provider=...)``.

The subclass inherits everything vendor-neutral — prompt loading, image
encoding, the single JSON retry and the Pydantic schema validation — and changes
only what is genuinely Qwen/DashScope-specific:

* ``reasoning_effort="none"`` is a DashScope/OpenAI parameter GLM's compatible
  endpoint does not document, so it is stripped before the request.
* ``max_tokens`` is clamped to ``GLM_MAX_TOKENS``. The default is 32768 because
  text-heavy screenshots can produce a cross-modal JSON payload longer than
  16384 tokens; clipping it turns a valid image observation into invalid JSON.
* The request keeps the engine's MIME-aware ``data:`` URI by default. Bare
  base64 can work for some JPEG inputs, but GLM returned empty content for
  browser-ingested WebP images until the MIME type was included.
* ``provider_name`` / ``prompt_version`` are relabelled so a trace never claims
  Qwen ran when GLM did.

Known limit: without a GLM key this path cannot be exercised live, so the
request-shape choices above are documented assumptions rather than verified
facts. See ``docs/API_CONTRACT.md`` §6.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)

# -- optional dependency (the engine package) --------------------------------
_IMPORT_ERROR: str | None = None
_BaseVisionClient: type | None = None
try:  # pragma: no cover - presence depends on the local engine install
    from content_trust.providers.base import ProviderError as _ProviderError
    from content_trust.providers.qwen_vision import QwenVisionClient as _BaseVisionClient
except Exception as exc:  # noqa: BLE001
    _IMPORT_ERROR = str(exc)

GLM_AVAILABLE = _BaseVisionClient is not None


# -- request-shaping proxies -------------------------------------------------


class _CompletionsProxy:
    """Strips unsupported kwargs and clamps ``max_tokens``."""

    def __init__(self, completions: Any, *, drop: set[str], max_tokens: int | None) -> None:
        self._completions = completions
        self._drop = drop
        self._max_tokens = max_tokens

    def create(self, **kwargs: Any) -> Any:
        for key in self._drop:
            kwargs.pop(key, None)
        if self._max_tokens is not None:
            requested = kwargs.get("max_tokens")
            if requested is None or requested > self._max_tokens:
                kwargs["max_tokens"] = self._max_tokens
        return self._completions.create(**kwargs)


class _ChatProxy:
    def __init__(self, chat: Any, *, drop: set[str], max_tokens: int | None) -> None:
        self.completions = _CompletionsProxy(
            chat.completions, drop=drop, max_tokens=max_tokens
        )


class _ClientProxy:
    """Minimal duck-typed stand-in for ``openai.OpenAI``."""

    def __init__(self, inner: Any, *, drop: set[str], max_tokens: int | None) -> None:
        self.chat = _ChatProxy(inner.chat, drop=drop, max_tokens=max_tokens)


# Parameters the DashScope endpoint accepts but GLM's compatible API does not document.
_DROP_PARAMS = {"reasoning_effort"}


if GLM_AVAILABLE:

    class GLMVisionClient(_BaseVisionClient):  # type: ignore[misc,valid-type]
        """Zhipu GLM adapter for per-image visual review and screenshot intake."""

        provider_name = "glm_vision"
        prompt_version = "glm_v1"
        screenshot_prompt_version = "glm_screenshot_v1"

        def __init__(self, settings: Settings) -> None:
            api_key = settings.glm_api_key
            if not api_key:
                raise _ProviderError(self.provider_name, "provider_not_configured")

            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise _ProviderError(
                    self.provider_name, "dependency_missing", "Install openai"
                ) from exc

            inner = OpenAI(
                api_key=api_key,
                base_url=settings.glm_base_url,
                timeout=settings.glm_timeout_seconds,
                # Free GLM tiers rate-limit hard; a bare 429 would surface as
                # "image dimension unavailable". The SDK retries 429/5xx with
                # exponential backoff and honours Retry-After.
                max_retries=max(0, settings.glm_max_retries),
            )
            client = _ClientProxy(
                inner, drop=_DROP_PARAMS, max_tokens=settings.glm_max_tokens
            )
            # The parent accepts a prebuilt client and skips its Qwen key check.
            super().__init__(settings, client=client)
            self.model_name = settings.glm_model
            self._image_format = (settings.glm_image_url_format or "data_uri").strip().lower()

        def _image_data_url(self, image_path: str | None, image_url: str | None) -> str:
            resolved = super()._image_data_url(image_path, image_url)
            if self._image_format == "raw" and resolved.startswith("data:"):
                # Zhipu examples use raw base64 (no data-URI prefix).
                return resolved.split(",", 1)[1]
            return resolved

        def _complete_json(self, instructions: str, user_content: list[dict[str, Any]], response_model: type) -> Any:
            """Retry one extra full round when GLM emits empty/truncated JSON.

            Reasoning-heavy screenshots occasionally exhaust one round of output
            without leaving a parseable JSON object. The parent already retries
            twice with corrective feedback; one additional round turns those
            transient empty responses into a usable image observation instead of
            marking the whole R dimension unavailable.
            """
            try:
                return super()._complete_json(instructions, user_content, response_model)
            except _ProviderError as exc:
                if getattr(exc, "code", None) not in {"empty_response", "invalid_json"}:
                    raise
                logger.warning("GLM vision returned %s; retrying one full JSON round", exc.code)
                return super()._complete_json(instructions, user_content, response_model)

else:  # pragma: no cover - the engine package is absent
    GLMVisionClient = None  # type: ignore[assignment,misc]


def build_glm_vision(settings: Settings) -> Any | None:
    """Return a configured GLM vision provider, or ``None`` when unusable."""
    if not GLM_AVAILABLE:
        logger.warning("GLM vision requested but the engine package is missing (%s)", _IMPORT_ERROR)
        return None
    if not settings.glm_api_key:
        return None
    try:
        return GLMVisionClient(settings)
    except Exception:  # noqa: BLE001 - never let vision setup break the app
        logger.exception("GLM vision provider initialization failed")
        return None
