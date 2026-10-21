"""Normalized response schemas for analyzers and the public report."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from content_trust.schemas.evidence import EvidenceItem
from content_trust.schemas.registry import ProductRegistryMatch

FindingCategory = Literal[
    "ai_generated_text",
    "exaggerated_claim",
    "unsupported_claim",
    "contradicted_claim",
    "semantic_anomaly",
    "fake_endorsement_risk",
    "ai_generated_image",
    "localized_image_manipulation",
    "image_text_mismatch",
]
FindingVerdict = Literal[
    "supported",
    "suspicious",
    "contradicted",
    "insufficient_evidence",
    "unknown",
]
FindingSeverity = Literal["info", "low", "medium", "high"]
FindingSource = Literal["llm", "vlm", "image_forensics", "ai_text_detector", "rule"]
ClaimType = Literal[
    "efficacy",
    "numeric",
    "time_bound",
    "medical",
    "safety",
    "ingredient",
    "endorsement",
    "comparative",
    "absolute",
    "product_identity",
    "other",
]
ClaimVerificationStatus = Literal["supported", "contradicted", "insufficient_evidence"]
TextIntegritySignalType = Literal[
    "style_shift",
    "repetition",
    "internal_contradiction",
    "abrupt_transition",
]
TextIntegrityStatus = Literal[
    "signals_found",
    "no_clear_signal",
    "insufficient_context",
]
TextIntegrityStrength = Literal["weak", "moderate"]
ReviewPriority = Literal["low", "review", "high"]
CrossModalStatus = Literal["MATCH", "MISMATCH", "NOT_VERIFIABLE", "UNCERTAIN"]
CommentVerdict = Literal["low_risk", "suspicious", "uncertain"]
EvidenceMatchStatus = Literal["matched", "mismatch", "unknown", "not_applicable"]
ProvenanceStatus = Literal["valid", "invalid", "absent", "unavailable"]
EvidenceDimension = Literal[
    "provenance",
    "visual_forensics",
    "semantic_consistency",
    "claim_evidence",
    "text_provenance",
    "comment_anomaly",
]


class ClaimEvidenceAlignment(BaseModel):
    """Explicitly records which parts of a claim an evidence item can match."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "direct_evidence",
        "indirect_evidence",
        "related_only",
        "not_matched",
        "insufficient_evidence",
    ]
    product_formula: EvidenceMatchStatus = "unknown"
    population: EvidenceMatchStatus = "unknown"
    endpoint: EvidenceMatchStatus = "unknown"
    method: EvidenceMatchStatus = "unknown"
    duration: EvidenceMatchStatus = "unknown"
    comparator: EvidenceMatchStatus = "unknown"
    result: EvidenceMatchStatus = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class LocalizationBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def max_coordinates_follow_minimums(self) -> "LocalizationBox":
        if self.x_max < self.x_min or self.y_max < self.y_min:
            raise ValueError("Bounding-box max coordinates must follow min coordinates")
        return self


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: FindingCategory
    verdict: FindingVerdict
    severity: FindingSeverity
    risk_score: float | None = Field(default=None, ge=0.0, le=1.0)
    signal_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_semantics: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    observation: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    source: FindingSource
    recommended_action: str

    @model_validator(mode="after")
    def contradiction_must_reference_evidence(self) -> "Finding":
        if (
            self.category == "contradicted_claim"
            and self.verdict == "contradicted"
            and not self.evidence_ids
        ):
            raise ValueError("A contradicted claim must cite evidence_ids")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str
    claim_type: ClaimType
    verification_status: ClaimVerificationStatus | None = None
    risk_score: float = Field(ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    observation: str = ""
    reason: str = ""
    subject: str | None = None
    metric: str | None = None
    value: str | float | None = None
    unit: str | None = None
    duration: str | None = None
    population: str | None = None
    method: str | None = None
    comparator: str | None = None
    evidence_required: bool = True
    evidence_alignment: ClaimEvidenceAlignment | None = None

    @model_validator(mode="after")
    def verified_claim_must_reference_evidence(self) -> "Claim":
        if (
            self.verification_status in {"supported", "contradicted"}
            and not self.evidence_ids
        ):
            raise ValueError("Supported or contradicted claims must cite evidence_ids")
        return self


class AITextSignal(BaseModel):
    """Weak AI-style/provenance signal; it is not a calibrated probability."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    ai_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    human_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provider: str | None = None
    reason: str | None = None
    explanation: str | None = None
    observed_cues: list[str] = Field(default_factory=list)
    score_semantics: str | None = None


class TextIntegritySignal(BaseModel):
    """A localized wording or consistency cue, not proof of authorship or editing."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    signal_type: TextIntegritySignalType
    span: str = Field(min_length=1)
    related_span: str | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    related_start_char: int | None = Field(default=None, ge=0)
    related_end_char: int | None = Field(default=None, ge=0)
    observation: str
    reason: str
    benign_alternative: str
    strength: TextIntegrityStrength = "weak"
    source: Literal["llm", "rule"] = "llm"

    @model_validator(mode="after")
    def spans_must_have_paired_offsets(self) -> "TextIntegritySignal":
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("span offsets must be provided together")
        if (self.related_start_char is None) != (self.related_end_char is None):
            raise ValueError("related span offsets must be provided together")
        if self.related_span is None and (
            self.related_start_char is not None or self.related_end_char is not None
        ):
            raise ValueError("related span offsets require related_span")
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char < self.start_char
        ):
            raise ValueError("end_char must not precede start_char")
        if (
            self.related_start_char is not None
            and self.related_end_char is not None
            and self.related_end_char < self.related_start_char
        ):
            raise ValueError("related_end_char must not precede related_start_char")
        return self


class TextIntegrityAnalysis(BaseModel):
    """Weak style/structure review, kept separate from factual claim analysis."""

    model_config = ConfigDict(extra="forbid")

    status: TextIntegrityStatus = "insufficient_context"
    signals: list[TextIntegritySignal] = Field(default_factory=list)
    model_reviewed: bool = False


class ExplanationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    recommended_actions: list[str] = Field(default_factory=list)


class TextAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[Claim] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    ai_text_signal: AITextSignal | None = None
    semantic_anomaly_score: float | None = Field(default=None, ge=0.0, le=1.0)
    style_anomalies: list[str] = Field(default_factory=list)
    text_integrity: TextIntegrityAnalysis = Field(default_factory=TextIntegrityAnalysis)
    provider_error: str | None = None


class AIGenerationSignal(BaseModel):
    """Experimental whole-image AI-generation detector signal."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    available: bool
    score_semantics: str = "uncalibrated AI-generation score, not a probability"
    inference_device: Literal["cuda", "mps", "cpu"] | None = None
    error: str | None = None


class TruForSignal(BaseModel):
    """Normalized global and local TruFor signals, neither calibrated for beauty posts."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "trufor_local"
    available: bool
    detection_score: float | None = Field(default=None, ge=0.0, le=1.0)
    localization_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reliability_mean: float | None = Field(default=None, ge=0.0, le=1.0)
    localized_region_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    localization_map_path: str | None = None
    overlay_path: str | None = None
    score_semantics: str = (
        "uncalibrated TruFor image/localization signal; not a probability"
    )
    inference_device: Literal["cuda", "mps", "cpu"] | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)
    error: str | None = None


class CommentAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment_id: str
    verdict: CommentVerdict
    risk_score: float = Field(ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    indicators: list[str] = Field(default_factory=list)
    observation: str
    reason: str
    recommended_action: str


class CommentPatternCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    pattern_type: Literal[
        "repeated_template",
        "near_duplicate",
        "irrelevant_promotion",
        "contact_or_link",
        "keyword_stuffing",
        "sampled_unique_comments",
    ]
    member_comment_ids: list[str] = Field(default_factory=list)
    count: int = Field(ge=1)
    representative_text: str
    examples: list[str] = Field(default_factory=list)
    source: Literal["rule", "llm"] = "rule"
    verdict: CommentVerdict = "uncertain"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""
    recommended_action: str = ""


class CommentAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    assessed_count: int = Field(default=0, ge=0)
    total_comment_count: int = Field(default=0, ge=0)
    llm_input_count: int = Field(default=0, ge=0)
    suspicious_pattern_count: int = Field(default=0, ge=0)
    model_reviewed: bool = False
    assessments: list[CommentAssessment] = Field(default_factory=list)
    pattern_clusters: list[CommentPatternCluster] = Field(default_factory=list)
    cross_comment_patterns: list[str] = Field(default_factory=list)
    sample_scope: str | None = None
    summary: str = ""
    error: str | None = None


class ImageForensicsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    ai_generation_signal: AIGenerationSignal | None = None
    nfa_status: Literal["available", "unavailable", "not_configured"] = "not_configured"
    nfa_error: str | None = None
    localized_manipulation_score: float | None = Field(default=None, ge=0.0, le=1.0)
    localized_region_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    localized_region_bbox: LocalizationBox | None = None
    localized_overlay_path: str | None = None
    localization_score_method: str | None = None
    trufor_analysis: TruForSignal | None = None
    score_semantics: str | None = None
    inference_device: Literal["cuda", "mps", "cpu"] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    available: bool
    error: str | None = None
    provider_errors: list[str] = Field(default_factory=list)
    # Kept internally for adapter/debug use but omitted from report JSON/UI.
    raw_response: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)


class CrossModalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CrossModalStatus
    available: bool = True
    visible_text: list[str] = Field(default_factory=list)
    observations: list["VisualObservation"] = Field(default_factory=list)
    image_text_claims: list[Claim] = Field(default_factory=list)
    image_text_findings: list[Finding] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    error: str | None = None

    @model_validator(mode="after")
    def mismatch_has_a_vlm_finding(self) -> "CrossModalResult":
        mismatch_findings = [
            item for item in self.findings if item.category == "image_text_mismatch"
        ]
        if self.status == "MISMATCH":
            if not mismatch_findings:
                raise ValueError("MISMATCH requires an image_text_mismatch finding")
            if any(item.source != "vlm" for item in mismatch_findings):
                raise ValueError("Cross-modal findings must identify source=vlm")
        elif mismatch_findings:
            raise ValueError("Only MISMATCH results may contain image_text_mismatch findings")
        if any(item.source != "vlm" for item in self.image_text_findings):
            raise ValueError("Image-text findings must identify source=vlm")
        return self


class VisualObservation(BaseModel):
    """A directly visible detail recorded before semantic comparison."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    observation: str
    visible_text: str | None = None
    region: str | None = None
    source: Literal["vlm"] = "vlm"


class ProvenanceAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProvenanceStatus
    provider: str = "c2pa-python"
    manifest_present: bool | None = None
    validation_state: str | None = None
    active_manifest: str | None = None
    claim_generator: str | None = None
    signature_issuer: str | None = None
    signature_time: str | None = None
    actions: list[str] = Field(default_factory=list)
    ingredients_count: int | None = Field(default=None, ge=0)
    error: str | None = None
    exif_available: bool = False
    exif_present: bool | None = None
    exif_camera_make: str | None = None
    exif_camera_model: str | None = None
    exif_datetime_original: str | None = None
    exif_software: str | None = None
    exif_error: str | None = None


class ClaimEvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_claims: int = Field(default=0, ge=0)
    evidence_required: int = Field(default=0, ge=0)
    supported: int = Field(default=0, ge=0)
    contradicted: int = Field(default=0, ge=0)
    insufficient_evidence: int = Field(default=0, ge=0)
    not_assessed: int = Field(default=0, ge=0)


class EvidenceCard(BaseModel):
    """Compact, UI-ready evidence from one independent analysis dimension."""

    model_config = ConfigDict(extra="forbid")

    dimension: EvidenceDimension
    status: str
    title: str
    observations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    signal_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_semantics: str | None = None
    limitations: list[str] = Field(default_factory=list)


class TrustReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: str
    schema_version: str = "2.1"
    review_priority: ReviewPriority
    provenance_analysis: ProvenanceAnalysis | None = None
    claim_evidence_summary: ClaimEvidenceSummary = Field(default_factory=ClaimEvidenceSummary)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    # Kept as an empty compatibility field; NMPA lookup is no longer active.
    product_registry_matches: list[ProductRegistryMatch] = Field(default_factory=list)
    text_analysis: TextAnalysisResult | None = None
    comment_analysis: CommentAnalysisResult | None = None
    image_analysis: ImageForensicsResult | None = None
    crossmodal_analysis: CrossModalResult | None = None
    summary: str
    recommended_actions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    model_metadata: dict[str, Any] = Field(default_factory=dict)


class ImageTrustResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_id: str
    image_analysis: ImageForensicsResult
    provenance_analysis: ProvenanceAnalysis | None = None
    crossmodal_analysis: CrossModalResult
    findings: list[Finding] = Field(default_factory=list)


class BatchTrustReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: str
    schema_version: str = "2.1"
    review_priority: ReviewPriority
    claim_evidence_summary: ClaimEvidenceSummary = Field(default_factory=ClaimEvidenceSummary)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    # Kept as an empty compatibility field; NMPA lookup is no longer active.
    product_registry_matches: list[ProductRegistryMatch] = Field(default_factory=list)
    text_analysis: TextAnalysisResult | None = None
    comment_analysis: CommentAnalysisResult | None = None
    images: list[ImageTrustResult] = Field(default_factory=list)
    summary: str
    recommended_actions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    model_metadata: dict[str, Any] = Field(default_factory=dict)
