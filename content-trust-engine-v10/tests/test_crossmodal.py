from content_trust.analyzers.crossmodal import CrossModalAnalyzer
from content_trust.schemas import Claim, CrossModalResult, Finding


class FakeCrossModalProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def analyze_consistency(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_crossmodal_mismatch_requires_explicit_visual_finding() -> None:
    provider = FakeCrossModalProvider(
        CrossModalResult(
            status="MISMATCH",
            findings=[
                Finding(
                    id="mismatch-1",
                    category="image_text_mismatch",
                    verdict="contradicted",
                    severity="high",
                    risk_score=0.9,
                    observation="图片瓶身可读标签为 Y Cream，文案写 X Serum。",
                    reason="可见产品名与文案冲突。",
                    source="vlm",
                    recommended_action="人工确认包装版本。",
                )
            ],
            summary="可见产品名冲突。",
        )
    )
    analyzer = CrossModalAnalyzer(provider)

    result = analyzer.analyze(
        image_url="https://example.test/product.jpg",
        image_path=None,
        text="这是 X Serum",
        claims=[Claim(id="c1", text="X Serum", claim_type="product_identity", risk_score=0.0)],
    )

    assert result.status == "MISMATCH"
    assert provider.calls == 1


def test_crossmodal_returns_not_verifiable_without_both_modalities() -> None:
    provider = FakeCrossModalProvider()
    analyzer = CrossModalAnalyzer(provider)

    result = analyzer.analyze(
        image_path=None,
        image_url=None,
        text="7天淡纹",
        claims=[],
    )

    assert result.status == "NOT_VERIFIABLE"
    assert result.available is False
    assert provider.calls == 0


def test_crossmodal_provider_failure_degrades_to_uncertain() -> None:
    provider = FakeCrossModalProvider(error=TimeoutError("timeout"))
    analyzer = CrossModalAnalyzer(provider)

    result = analyzer.analyze(
        image_path=None,
        image_url="https://example.test/product.jpg",
        text="一瓶精华",
        claims=[],
    )

    assert result.status == "UNCERTAIN"
    assert result.available is False
    assert result.error == "provider_timeout"
