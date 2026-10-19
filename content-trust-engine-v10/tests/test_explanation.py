from content_trust.fusion.explain import ExplanationBuilder
from content_trust.schemas import EvidenceCard, EvidenceItem, Finding


def test_explanation_fallback_separates_observation_evidence_and_inference() -> None:
    finding = Finding(
        id="f1",
        category="contradicted_claim",
        verdict="contradicted",
        severity="high",
        risk_score=0.9,
        confidence=0.95,
        observation="文案写7天改善80%。",
        reason="所提供报告记录的是4周改善18%，数字和时间均冲突。",
        evidence_ids=["official-study"],
        source="llm",
        recommended_action="人工检查广告原稿和完整报告。",
    )

    result, error = ExplanationBuilder().build(
        findings=[finding],
        evidence_items=[
            EvidenceItem(
                id="official-study",
                source="官方产品报告",
                text="4周改善18%",
            )
        ],
        review_priority="high",
        evidence_cards=[],
    )

    assert error is None
    assert "观察到：" in result.summary
    assert "引用证据：" in result.summary
    assert "解释与局限：" in result.summary
    assert "该优先级不是真假结论" in result.summary
    assert result.recommended_actions == ["人工检查广告原稿和完整报告。"]


def test_explanation_provider_failure_uses_local_summary() -> None:
    class FailedProvider:
        def explain(self, **kwargs):
            raise TimeoutError("timed out")

    result, error = ExplanationBuilder(FailedProvider()).build(
        findings=[],
        evidence_items=[],
        review_priority="low",
        evidence_cards=[
            EvidenceCard(
                dimension="claim_evidence",
                status="unknown",
                title="Claim evidence",
            )
        ],
    )

    assert error == "provider_error"
    assert "没有产生可报告的具体 finding" in result.summary
    assert result.recommended_actions
