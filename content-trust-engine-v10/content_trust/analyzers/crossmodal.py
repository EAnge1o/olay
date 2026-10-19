"""Semantic image-text comparison, separate from localized image forensics."""

from content_trust.providers.base import CrossModalProvider
from content_trust.schemas import Claim, CrossModalResult, EvidenceItem


class CrossModalAnalyzer:
    def __init__(self, provider: CrossModalProvider | None = None) -> None:
        self._provider = provider

    def analyze(
        self,
        *,
        image_path: str | None,
        image_url: str | None,
        text: str | None,
        claims: list[Claim],
        evidence_items: list[EvidenceItem] | None = None,
    ) -> CrossModalResult:
        if image_path is None and image_url is None:
            return CrossModalResult(
                status="NOT_VERIFIABLE",
                available=False,
                summary="An image is required for visual text analysis.",
            )
        if self._provider is None:
            return CrossModalResult(
                status="UNCERTAIN",
                available=False,
                error="provider_not_configured",
                summary="Visual consistency could not be checked because Qwen Vision is not configured.",
            )
        try:
            return self._provider.analyze_consistency(
                image_path=image_path,
                image_url=image_url,
                text=text,
                claims=claims,
                evidence_items=evidence_items or [],
            )
        except Exception as exc:
            error = getattr(exc, "code", None)
            if not error or error == "provider_error":
                if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
                    error = "provider_timeout"
                elif "connection" in type(exc).__name__.lower():
                    error = "provider_connection_error"
                else:
                    error = f"provider_error_{type(exc).__name__.lower()}"
            return CrossModalResult(
                status="UNCERTAIN",
                available=False,
                error=error,
                summary="Visual consistency could not be checked.",
            )
