"""OpenAI-compatible semantic model adapter for text and VLM analysis."""

import base64
import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from content_trust.config import Settings
from content_trust.providers.base import ProviderError
from content_trust.schemas.agent import (
    AccountAnalysisResult,
    AccountHistoryInput,
    FollowUpResult,
)
from content_trust.schemas import (
    Claim,
    CrossModalResult,
    CommentAnalysisResult,
    CommentPatternCluster,
    EvidenceItem,
    ExplanationResult,
    EvidenceCard,
    Finding,
    ImageTrustResult,
    TextAnalysisResult,
)

T = TypeVar("T", bound=BaseModel)
_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class DeepSeekClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        prompt_dir: Path = _PROMPT_DIR,
    ) -> None:
        self.provider_name = "deepseek"
        if not settings.deepseek_api_key and client is None:
            raise ProviderError(self.provider_name, "provider_not_configured")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ProviderError(
                    self.provider_name, "dependency_missing", "Install openai"
                ) from exc
            client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.timeout_seconds,
                max_retries=0,
            )
        self._client = client
        self._model = settings.deepseek_model
        self.model_name = settings.deepseek_model
        self.claim_prompt_version = "v10"
        self.reasoning_effort = "none"
        self._prompt_dir = prompt_dir

    def analyze_claims(
        self, text: str, evidence_items: list[EvidenceItem]
    ) -> TextAnalysisResult:
        return self.analyze_claims_localized(text, evidence_items, locale="zh-CN")

    def analyze_claims_localized(
        self,
        text: str,
        evidence_items: list[EvidenceItem],
        *,
        locale: str = "zh-CN",
    ) -> TextAnalysisResult:
        prompt = self._read_prompt("claim_analysis_v10.txt")
        payload = {
            "text": text,
            "locale": locale,
            "evidence_items": [item.model_dump(mode="json") for item in evidence_items],
        }
        return self._complete_json(
            prompt,
            json.dumps(payload, ensure_ascii=False),
            TextAnalysisResult,
            max_tokens=8192,
        )

    def analyze_account_history(
        self,
        history: AccountHistoryInput,
        *,
        locale: str = "zh-CN",
    ) -> AccountAnalysisResult:
        prompt = self._read_prompt("account_analysis_v1.txt")
        payload = {"locale": locale, "account_history": history.model_dump(mode="json")}
        return self._complete_json(
            prompt,
            json.dumps(payload, ensure_ascii=False),
            AccountAnalysisResult,
            max_tokens=4096,
        )

    def answer_follow_up(
        self,
        question: str,
        task_context: dict[str, Any],
        *,
        task_id: str,
        locale: str = "zh-CN",
    ) -> FollowUpResult:
        prompt = self._read_prompt("follow_up_v1.txt")
        payload = {
            "task_id": task_id,
            "locale": locale,
            "question": question,
            "task_context": task_context,
        }
        return self._complete_json(
            prompt,
            json.dumps(payload, ensure_ascii=False),
            FollowUpResult,
            max_tokens=2048,
        )

    def analyze_comment_patterns(
        self,
        clusters: list[CommentPatternCluster],
        *,
        total_comment_count: int,
    ) -> CommentAnalysisResult:
        prompt = self._read_prompt("comment_analysis_v3.txt")
        payload = {
            "total_comment_count": total_comment_count,
            "llm_input_count": len(clusters),
            "pattern_clusters": [
                {
                    "cluster_id": cluster.cluster_id,
                    "pattern_type": cluster.pattern_type,
                    "count": cluster.count,
                    "representative_text": cluster.representative_text[:1200],
                    "examples": [text[:500] for text in cluster.examples[:3]],
                    "reason": cluster.reason[:500],
                }
                for cluster in clusters
            ],
        }
        return self._complete_json(
            prompt,
            json.dumps(payload, ensure_ascii=False),
            CommentAnalysisResult,
            max_tokens=8192,
        )

    def analyze_consistency(
        self,
        *,
        image_path: str | None = None,
        image_url: str | None = None,
        text: str | None,
        claims: list[Claim],
        evidence_items: list[EvidenceItem] | None = None,
    ) -> CrossModalResult:
        prompt = self._read_prompt("crossmodal_v8.txt")
        text_payload = {
            "text": text,
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "evidence_items": [
                item.model_dump(mode="json") for item in (evidence_items or [])
            ],
        }
        image_data_url = self._image_data_url(image_path, image_url)
        image_part = {
            "type": "image_url",
            "image_url": {"url": image_data_url},
        }
        content = [
            {
                "type": "text",
                "text": "Compare this JSON input:\n"
                + json.dumps(text_payload, ensure_ascii=False),
            },
            image_part,
        ]
        return self._complete_json(
            prompt, content, CrossModalResult, max_tokens=8192
        )

    def explain(
        self,
        *,
        findings: list[Finding],
        evidence_items: list[EvidenceItem],
        review_priority: str,
        evidence_cards: list[EvidenceCard],
        image_results: list[ImageTrustResult] | None = None,
        comment_analysis: CommentAnalysisResult | None = None,
    ) -> ExplanationResult:
        return self.explain_localized(
            findings=findings,
            evidence_items=evidence_items,
            review_priority=review_priority,
            evidence_cards=evidence_cards,
            image_results=image_results,
            comment_analysis=comment_analysis,
            locale="zh-CN",
        )

    def explain_localized(
        self,
        *,
        findings: list[Finding],
        evidence_items: list[EvidenceItem],
        review_priority: str,
        evidence_cards: list[EvidenceCard],
        image_results: list[ImageTrustResult] | None = None,
        comment_analysis: CommentAnalysisResult | None = None,
        locale: str = "zh-CN",
    ) -> ExplanationResult:
        payload = {
            "locale": locale,
            "findings": [finding.model_dump(mode="json") for finding in findings],
            "evidence_items": [item.model_dump(mode="json") for item in evidence_items],
            "image_analyses": [
                {
                    "image_id": image.image_id,
                    "forensics": image.image_analysis.model_dump(
                        mode="json", exclude={"raw_response"}
                    ),
                    "visible_text": image.crossmodal_analysis.visible_text,
                    "observations": [
                        item.model_dump(mode="json")
                        for item in image.crossmodal_analysis.observations
                    ],
                    "image_text_claims": [
                        claim.model_dump(mode="json")
                        for claim in image.crossmodal_analysis.image_text_claims
                    ],
                    "consistency_status": image.crossmodal_analysis.status,
                    "consistency_summary": image.crossmodal_analysis.summary,
                }
                for image in (image_results or [])
            ],
            "comment_analysis": (
                comment_analysis.model_dump(mode="json")
                if comment_analysis is not None
                else None
            ),
            "review_priority": review_priority,
            "evidence_cards": [
                card.model_dump(mode="json") for card in evidence_cards
            ],
        }
        prompt = self._read_prompt("explanation_v10.txt")
        return self._complete_json(
            prompt,
            json.dumps(payload, ensure_ascii=False),
            ExplanationResult,
            max_tokens=2048,
        )

    def _complete_json(
        self,
        instructions: str,
        user_content: str | list[dict[str, Any]],
        response_model: type[T],
        *,
        max_tokens: int,
    ) -> T:
        request_content = user_content
        request_max_tokens = max_tokens
        for attempt in range(2):
            try:
                request_options = {
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": request_content},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": request_max_tokens,
                }
                request_options["reasoning_effort"] = self.reasoning_effort
                response = self._client.chat.completions.create(**request_options)
            except Exception as exc:
                if attempt == 0:
                    continue
                raise ProviderError(
                    self.provider_name,
                    _exception_code(exc),
                    f"{self.provider_name} request failed",
                ) from exc

            choices = getattr(response, "choices", None)
            if not choices:
                failure = ProviderError(
                    self.provider_name,
                    "invalid_response",
                    f"{self.provider_name} returned no choices",
                )
                if attempt == 0:
                    request_content = _append_retry_feedback(
                        request_content, "The previous response had no choices."
                    )
                    continue
                raise failure

            choice = choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                if attempt == 0:
                    request_max_tokens *= 2
                    request_content = _append_retry_feedback(
                        request_content,
                        "The previous JSON was cut off. Return a compact complete JSON object.",
                    )
                    continue
                raise ProviderError(
                    self.provider_name,
                    "response_truncated",
                    f"finish_reason=length at max_tokens={request_max_tokens}",
                )

            try:
                content = choice.message.content
                if not content:
                    raise _ResponseFormatError(
                        "empty_response", "Semantic model returned empty content"
                    )
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise _ResponseFormatError(
                        "invalid_json", "JSON root must be an object"
                    )
            except json.JSONDecodeError as exc:
                failure = ProviderError(
                    self.provider_name,
                    "invalid_json",
                    f"JSON decode failed at line {exc.lineno}, column {exc.colno}",
                )
            except _ResponseFormatError as exc:
                failure = ProviderError(self.provider_name, exc.code, exc.detail)
            else:
                try:
                    return response_model.model_validate(parsed)
                except ValidationError as exc:
                    failure = ProviderError(
                        self.provider_name,
                        "schema_validation_failed",
                        _validation_error_summary(exc),
                    )

            if attempt == 0:
                request_content = _append_retry_feedback(
                    request_content,
                    f"The previous response failed validation ({failure.code}). "
                    f"Correct the output to match the requested JSON shape. {failure.detail}",
                )
                continue
            raise failure

        raise ProviderError(
            self.provider_name, "provider_error_no_response_after_retry"
        )

    def _read_prompt(self, filename: str) -> str:
        try:
            return (self._prompt_dir / filename).read_text(encoding="utf-8")
        except OSError as exc:
            raise ProviderError(self.provider_name, "prompt_unavailable", filename) from exc

    def _image_data_url(self, image_path: str | None, image_url: str | None) -> str:
        if image_url is not None:
            if not image_url.startswith(("http://", "https://", "data:image/")):
                raise ProviderError(self.provider_name, "invalid_image_url")
            if len(image_url) > 8192:
                raise ProviderError(self.provider_name, "image_url_too_long")
            return image_url
        if image_path is None:
            raise ProviderError(self.provider_name, "image_required")

        path = Path(image_path)
        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            raise ProviderError(self.provider_name, "image_unavailable") from exc
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            raise ProviderError(self.provider_name, "image_too_large")
        mime_type = _detect_image_mime(image_bytes)
        if mime_type not in _IMAGE_MIME_TYPES:
            raise ProviderError(self.provider_name, "unsupported_image_type")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"


class _ResponseFormatError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _append_retry_feedback(
    user_content: str | list[dict[str, Any]], feedback: str
) -> str | list[dict[str, Any]]:
    note = (
        "\n\nCorrection required: the previous response did not satisfy the requested "
        f"format. {feedback} Return only one valid json object."
    )
    if isinstance(user_content, str):
        return user_content + note

    content = [dict(part) for part in user_content]
    for part in content:
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            part["text"] += note
            return content
    return [{"type": "text", "text": note.strip()}, *content]


def _validation_error_summary(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False)
    issues = []
    for error in errors[:8]:
        location = ".".join(str(part) for part in error["loc"])
        issues.append(f"{location or '<root>'}:{error['type']}")
    suffix = "; ..." if len(errors) > 8 else ""
    return "Schema validation failed at " + ", ".join(issues) + suffix


def _detect_image_mime(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
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
    if name:
        return f"provider_error_{name}"
    return "provider_error"
