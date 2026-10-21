"""Synchronous interfaces for external model and forensic providers.

Protocols keep analyzers mockable and avoid coupling business logic to a vendor.
"""

from typing import Protocol

from content_trust.schemas import (
    AITextSignal,
    Claim,
    CommentAnalysisResult,
    CommentPatternCluster,
    CrossModalResult,
    ExplanationResult,
    EvidenceCard,
    EvidenceItem,
    Finding,
    ImageForensicsResult,
    ImageTrustResult,
    TextAnalysisResult,
)


class ProviderError(RuntimeError):
    """Readable, provider-neutral error raised by concrete adapters."""

    def __init__(self, provider: str, code: str, message: str | None = None) -> None:
        self.provider = provider
        self.code = code
        detail = message or code
        self.detail = detail
        super().__init__(f"{provider}: {detail}")


class ClaimAnalysisProvider(Protocol):
    """Extract claims and assess text risks using only supplied evidence."""

    def analyze_claims(
        self, text: str, evidence_items: list[EvidenceItem]
    ) -> TextAnalysisResult: ...


class ImageForensicsProvider(Protocol):
    """Return normalized localized image forensic signals."""

    def analyze_image(
        self, *, image_path: str | None = None, image_url: str | None = None
    ) -> ImageForensicsResult: ...


class CommentAnalysisProvider(Protocol):
    """Review compressed local comment-pattern clusters, not account identity."""

    def analyze_comment_patterns(
        self,
        clusters: list[CommentPatternCluster],
        *,
        total_comment_count: int,
    ) -> CommentAnalysisResult: ...


class CrossModalProvider(Protocol):
    """Check visible semantic content against text; never infer pixel tampering."""

    def analyze_consistency(
        self,
        *,
        image_path: str | None = None,
        image_url: str | None = None,
        text: str | None,
        claims: list[Claim],
        evidence_items: list[EvidenceItem],
    ) -> CrossModalResult: ...


class AITextDetectorProvider(Protocol):
    """Optional weak signal for text provenance/style, not factuality."""

    def detect_ai_text(self, text: str) -> AITextSignal: ...


class ExplanationProvider(Protocol):
    """Turn normalized output and caller evidence into a user-facing explanation."""

    def explain(
        self,
        *,
        findings: list[Finding],
        evidence_items: list[EvidenceItem],
        image_results: list[ImageTrustResult],
        comment_analysis: CommentAnalysisResult | None,
        review_priority: str,
        evidence_cards: list[EvidenceCard],
    ) -> ExplanationResult: ...
