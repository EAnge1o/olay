"""Alibaba Cloud Qwen-VL adapter for per-image visual review."""

import base64
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from content_trust.config import Settings
from content_trust.providers.base import ProviderError
from content_trust.schemas import Claim, CrossModalResult, EvidenceItem
from content_trust.schemas.agent import ScreenshotIntakeResult

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
_MAX_DATA_URI_BYTES = 20 * 1024 * 1024
_MAX_RAW_IMAGE_BYTES = 14 * 1024 * 1024
_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


class QwenVisionClient:
    """Use Qwen-VL for visual observations and image-text comparison only."""

    provider_name = "qwen_vision"
    prompt_version = "qwen_v1"
    screenshot_prompt_version = "screenshot_v1"

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        prompt_dir: Path = _PROMPT_DIR,
    ) -> None:
        api_key = settings.qwen_api_key or settings.dashscope_api_key
        if not api_key and client is None:
            raise ProviderError(self.provider_name, "provider_not_configured")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ProviderError(
                    self.provider_name, "dependency_missing", "Install openai"
                ) from exc
            client = OpenAI(
                api_key=api_key,
                base_url=settings.qwen_vl_base_url,
                timeout=settings.qwen_vl_timeout_seconds,
                max_retries=0,
            )
        self._client = client
        self.model_name = settings.qwen_vl_model
        self._prompt_dir = prompt_dir

    def analyze_consistency(
        self,
        *,
        image_path: str | None = None,
        image_url: str | None = None,
        text: str | None,
        claims: list[Claim],
        evidence_items: list[EvidenceItem] | None = None,
    ) -> CrossModalResult:
        prompt = self._read_prompt("crossmodal_qwen_v1.txt")
        payload = {
            "text": text,
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "evidence_items": [
                item.model_dump(mode="json") for item in (evidence_items or [])
            ],
        }
        image_data_url = self._image_data_url(image_path, image_url)
        user_content = [
            {
                "type": "text",
                "text": "请根据这份 JSON 输入核对图片；即使 text 为 null，也继续记录图片直接观察。\nJSON input:\n"
                + json.dumps(payload, ensure_ascii=False),
            },
            {
                "type": "image_url",
                "image_url": {"url": image_data_url},
            },
        ]
        return self._complete_json(prompt, user_content, CrossModalResult)

    def analyze_screenshot(
        self,
        *,
        image_path: str | None = None,
        image_url: str | None = None,
        locale: str = "zh-CN",
    ) -> ScreenshotIntakeResult:
        """Classify a screenshot and extract only visible content with regions."""

        prompt = self._read_prompt("screenshot_intake_v1.txt")
        image_data_url = self._image_data_url(image_path, image_url)
        user_content = [
            {
                "type": "text",
                "text": "按以下输出语言读取截图，不要推测不可见信息。locale: "
                + locale,
            },
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
        return self._complete_json(prompt, user_content, ScreenshotIntakeResult)

    def _complete_json(
        self,
        instructions: str,
        user_content: list[dict[str, Any]],
        response_model: type[BaseModel],
    ) -> Any:
        request_content = user_content
        last_failure: ProviderError | None = None
        for attempt in range(2):
            try:
                response = self._client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": request_content},
                    ],
                    response_format={"type": "json_object"},
                    reasoning_effort="none",
                    max_tokens=8192,
                )
            except Exception as exc:
                code = _exception_code(exc)
                if attempt == 0 and _is_retryable(exc):
                    continue
                raise ProviderError(
                    self.provider_name, code, "Qwen Vision request failed"
                ) from exc

            choices = getattr(response, "choices", None)
            if not choices:
                last_failure = ProviderError(
                    self.provider_name, "invalid_response", "Qwen returned no choices"
                )
            else:
                choice = choices[0]
                raw_content = getattr(getattr(choice, "message", None), "content", None)
                content = _content_text(raw_content)
                if not content:
                    last_failure = ProviderError(
                        self.provider_name,
                        "empty_response",
                        "Qwen returned empty content",
                    )
                else:
                    try:
                        parsed = json.loads(content)
                        if not isinstance(parsed, dict):
                            raise ValueError("JSON root must be an object")
                    except (json.JSONDecodeError, ValueError) as exc:
                        last_failure = ProviderError(
                            self.provider_name,
                            "invalid_json",
                            f"Qwen JSON parse failed: {type(exc).__name__}",
                        )
                    else:
                        try:
                            return response_model.model_validate(parsed)
                        except ValidationError as exc:
                            last_failure = ProviderError(
                                self.provider_name,
                                "schema_validation_failed",
                                _validation_error_summary(exc),
                            )

            if attempt == 0:
                request_content = _append_retry_feedback(
                    request_content,
                    "The previous response was invalid. Return one compact JSON object "
                    "that exactly matches the requested schema.",
                )
                continue
            raise last_failure or ProviderError(
                self.provider_name, "invalid_response"
            )

        raise last_failure or ProviderError(self.provider_name, "invalid_response")

    def _read_prompt(self, filename: str) -> str:
        try:
            return (self._prompt_dir / filename).read_text(encoding="utf-8")
        except OSError as exc:
            raise ProviderError(
                self.provider_name, "prompt_unavailable", filename
            ) from exc

    def _image_data_url(self, image_path: str | None, image_url: str | None) -> str:
        if image_url is not None:
            if image_url.startswith("data:"):
                if not image_url.startswith(
                    ("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")
                ):
                    raise ProviderError(self.provider_name, "unsupported_image_type")
                if len(image_url.encode("utf-8")) > _MAX_DATA_URI_BYTES:
                    raise ProviderError(self.provider_name, "image_too_large")
                return image_url
            if not image_url.startswith(("http://", "https://")):
                raise ProviderError(self.provider_name, "invalid_image_url")
            if len(image_url) > 8192:
                raise ProviderError(self.provider_name, "image_url_too_long")
            return image_url
        if image_path is None:
            raise ProviderError(self.provider_name, "image_required")

        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError as exc:
            raise ProviderError(self.provider_name, "image_unavailable") from exc
        if len(image_bytes) > _MAX_RAW_IMAGE_BYTES:
            raise ProviderError(self.provider_name, "image_too_large")
        mime_type = _detect_image_mime(image_bytes)
        if mime_type not in _IMAGE_MIME_TYPES:
            raise ProviderError(self.provider_name, "unsupported_image_type")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{encoded}"
        if len(data_url) > _MAX_DATA_URI_BYTES:
            raise ProviderError(self.provider_name, "image_too_large")
        return data_url


def _append_retry_feedback(
    user_content: list[dict[str, Any]], feedback: str
) -> list[dict[str, Any]]:
    content = [dict(part) for part in user_content]
    for part in content:
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            part["text"] += "\n\nCorrection: " + feedback + " Output valid JSON only."
            return content
    return [{"type": "text", "text": feedback}, *content]


def _content_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        chunks = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        joined = "".join(chunks).strip()
        return joined or None
    return None


def _detect_image_mime(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(image_bytes) >= 12
        and image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def _exception_code(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "provider_timeout"
    if "connection" in name:
        return "provider_connection_error"
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status_code, int) and 100 <= status_code <= 599:
        return f"provider_http_{status_code}"
    return f"provider_error_{name or 'unknown'}"


def _is_retryable(exc: Exception) -> bool:
    code = _exception_code(exc)
    if code in {"provider_timeout", "provider_connection_error"}:
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
    return status_code == 429 or (isinstance(status_code, int) and status_code >= 500)


def _validation_error_summary(exc: ValidationError) -> str:
    issues = []
    errors = exc.errors(include_url=False)
    for error in errors[:8]:
        location = ".".join(str(part) for part in error["loc"])
        issues.append(f"{location or '<root>'}:{error['type']}")
    suffix = "; ..." if len(errors) > 8 else ""
    return "Schema validation failed at " + ", ".join(issues) + suffix
