from content_trust.fusion.assessment import review_priority
from content_trust.schemas import Claim, Finding, ProvenanceAnalysis, TextAnalysisResult


def test_raw_uncalibrated_image_signal_does_not_raise_review_priority() -> None:
    signal = Finding(
        id="image-score",
        category="ai_generated_image",
        verdict="unknown",
        severity="info",
        signal_score=0.99,
        score_semantics="uncalibrated signal, not a probability",
        observation="raw detector output",
        reason="unvalidated model score",
        source="image_forensics",
        recommended_action="compare with source history",
    )

    assert review_priority(findings=[signal]) == "low"


def test_ai_text_provenance_signal_alone_does_not_raise_review_priority() -> None:
    signal = Finding(
        id="ai-text",
        category="ai_generated_text",
        verdict="suspicious",
        severity="low",
        risk_score=0.2,
        observation="Optional detector raised a provenance signal.",
        reason="Weak style signal only.",
        source="ai_text_detector",
        recommended_action="Check provenance independently.",
    )

    assert review_priority(findings=[signal]) == "low"


def test_evidence_gap_requests_review_without_declaring_claim_false() -> None:
    text_analysis = TextAnalysisResult(
        claims=[
            Claim(
                id="claim-1",
                text="28天缩小毛孔40%",
                claim_type="numeric",
                verification_status="insufficient_evidence",
                risk_score=0.7,
                evidence_required=True,
            )
        ]
    )

    assert review_priority(findings=[], text_analysis=text_analysis) == "review"
    assert text_analysis.claims[0].verification_status == "insufficient_evidence"


def test_evidence_backed_contradiction_raises_priority() -> None:
    finding = Finding(
        id="claim-conflict",
        category="contradicted_claim",
        verdict="contradicted",
        severity="high",
        risk_score=0.9,
        observation="Claim conflicts with supplied study.",
        reason="The supplied evidence reports a different result.",
        evidence_ids=["study-1"],
        source="llm",
        recommended_action="Review the original study.",
    )

    assert review_priority(findings=[finding]) == "high"


def test_invalid_c2pa_manifest_raises_priority_but_missing_manifest_does_not() -> None:
    assert review_priority(
        findings=[], provenance_analysis=ProvenanceAnalysis(status="invalid")
    ) == "high"
    assert review_priority(
        findings=[], provenance_analysis=ProvenanceAnalysis(status="absent")
    ) == "low"

