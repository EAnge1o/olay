import json

from content_trust import ContentTrustEngine, TrustInput, analyze_content
from content_trust.config import Settings
from content_trust.providers.base import ProviderError
from content_trust.schemas import (
    AITextSignal,
    CrossModalResult,
    EvidenceItem,
    ExplanationResult,
    Finding,
    ImageForensicsResult,
    TextAnalysisResult,
)


class FakeClaimProvider:
    def __init__(self, result=None, error=None):
        self.result = result or TextAnalysisResult()
        self.error = error
        self.calls = []

    def analyze_claims(self, text, evidence_items):
        self.calls.append((text, evidence_items))
        if self.error:
            raise self.error
        return self.result


class FakeImageProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def analyze_image(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


class FakeCrossModalProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def analyze_consistency(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


class FakeAiTextProvider:
    def detect_ai_text(self, text):
        return AITextSignal(
            available=False,
            provider="test_detector",
            reason="provider_not_configured",
        )


class FakeExplanationProvider:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def explain(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return ExplanationResult(
            summary="依据已归一化 finding 和证据生成的说明。",
            recommended_actions=["人工检查。"],
        )


def make_engine(
    *,
    claims=None,
    images=None,
    crossmodal=None,
    explanation=None,
    ai_text=None,
):
    return ContentTrustEngine(
        Settings(),
        claim_provider=claims,
        image_provider=images,
        crossmodal_provider=crossmodal,
        explanation_provider=explanation,
        ai_text_detector=ai_text or FakeAiTextProvider(),
    )


def test_only_text_and_no_evidence_returns_valid_json_report() -> None:
    report = analyze_content(
        TrustInput(
            content_id="text-only",
            text="这款面霜质地清爽。",
        ),
        engine=make_engine(),
    )

    payload = report.model_dump(mode="json")
    json.dumps(payload, ensure_ascii=False)
    assert report.text_analysis is not None
    assert report.image_analysis is None
    assert report.crossmodal_analysis is None
    assert not any(f.verdict == "contradicted" for f in report.findings)
    assert report.review_priority == "low"
    assert report.text_analysis.claims[0].evidence_required is False


def test_default_engine_does_not_guess_ai_text_provenance() -> None:
    report = ContentTrustEngine(Settings()).analyze(
        TrustInput(content_id="no-ai-detector", text="事实正确的普通文案。")
    )

    assert report.text_analysis is not None
    signal = report.text_analysis.ai_text_signal
    assert signal is not None
    assert signal.available is False
    assert signal.score is None
    assert signal.reason == "provider_not_configured"
    assert report.review_priority == "low"


def test_only_image_returns_partial_report_when_nfa_vit_is_unavailable() -> None:
    image = FakeImageProvider(
        ImageForensicsResult(
            provider="nfa_vit_space",
            available=False,
            error="provider_not_configured",
        )
    )
    report = make_engine(images=image).analyze(
        TrustInput(content_id="image-only", image_url="https://example.test/a.jpg")
    )

    assert report.image_analysis is not None
    assert report.image_analysis.available is False
    assert report.text_analysis is None
    assert report.crossmodal_analysis is None
    assert report.model_metadata["provider_errors"] == []


def test_multimodal_report_combines_all_injected_providers() -> None:
    claim = FakeClaimProvider(
        TextAnalysisResult(
            claims=[
                {
                    "id": "claim-1",
                    "text": "包装为 X Serum",
                    "claim_type": "product_identity",
                    "risk_score": 0.1,
                }
            ]
        )
    )
    image = FakeImageProvider(
        ImageForensicsResult(
            provider="nfa_vit_space",
            available=True,
            localized_manipulation_score=0.1,
        )
    )
    cross = FakeCrossModalProvider(
        CrossModalResult(status="MATCH", summary="可见包装与描述一致。")
    )
    explanation = FakeExplanationProvider()
    report = make_engine(
        claims=claim,
        images=image,
        crossmodal=cross,
        explanation=explanation,
    ).analyze(
        TrustInput(
            content_id="multi",
            text="包装为 X Serum",
            image_url="https://example.test/x-serum.jpg",
        )
    )

    assert report.text_analysis is not None
    assert report.image_analysis is not None
    assert report.crossmodal_analysis.status == "MATCH"
    assert report.summary.startswith("依据")
    assert report.recommended_actions == ["人工检查。"]
    assert "evidence_cards" in explanation.calls[0]
    assert "overall_risk_score" not in explanation.calls[0]


def test_evidence_backed_contradiction_gets_high_review_priority() -> None:
    evidence = EvidenceItem(
        id="study-1",
        source="official study",
        text="连续使用4周，平均改善18%。",
    )
    claim_result = TextAnalysisResult(
        claims=[
            {
                "id": "claim-1",
                "text": "7天淡纹80%",
                "claim_type": "efficacy",
                "verification_status": "contradicted",
                "risk_score": 0.9,
                "confidence": 0.94,
                "evidence_ids": ["study-1"],
                "observation": "文案声称7天淡纹80%",
                "reason": "资料显示时间和数字不同。",
            }
        ],
        findings=[
            Finding(
                id="contradiction-1",
                category="contradicted_claim",
                verdict="contradicted",
                severity="high",
                risk_score=0.9,
                confidence=0.94,
                observation="文案声称7天淡纹80%。",
                reason="提供资料是4周改善18%，存在时间和数字冲突。",
                evidence_ids=["study-1"],
                source="llm",
                recommended_action="人工复核完整研究资料。",
            )
        ],
    )
    provider = FakeClaimProvider(claim_result)
    report = make_engine(claims=provider).analyze(
        TrustInput(
            content_id="contradiction",
            text="7天淡纹80%",
            evidence_items=[evidence],
        )
    )

    assert report.review_priority == "high"
    assert provider.calls[0][1][0].id == "study-1"
    assert "4周改善18%" in report.summary


def test_individual_provider_failures_keep_partial_report_serializable() -> None:
    claim = FakeClaimProvider(error=ProviderError("deepseek", "provider_timeout"))
    image = FakeImageProvider(error=ProviderError("nfa_vit_space", "provider_timeout"))
    cross = FakeCrossModalProvider(error=ProviderError("deepseek", "provider_timeout"))
    explanation = FakeExplanationProvider(error=TimeoutError("timeout"))
    report = make_engine(
        claims=claim,
        images=image,
        crossmodal=cross,
        explanation=explanation,
    ).analyze(
        TrustInput(
            content_id="degraded",
            text="7天淡纹80%",
            image_url="https://example.test/product.jpg",
        )
    )

    json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert report.text_analysis.provider_error == "provider_timeout"
    assert report.image_analysis.error == "provider_timeout"
    assert report.crossmodal_analysis.status == "UNCERTAIN"
    assert "deepseek:provider_timeout" in report.model_metadata["provider_errors"]
    assert "image_forensics:provider_timeout" in report.model_metadata["provider_errors"]
    assert report.summary
