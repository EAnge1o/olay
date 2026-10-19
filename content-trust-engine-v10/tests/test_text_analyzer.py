from types import SimpleNamespace

import pytest

from content_trust.analyzers.text import TextAnalyzer
from content_trust.config import Settings
from content_trust.providers.base import ProviderError
from content_trust.providers.deepseek import DeepSeekClient
from content_trust.schemas import (
    EvidenceItem,
    TextAnalysisResult,
    TextIntegrityAnalysis,
    TextIntegritySignal,
)


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.requests = []

    def create(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response))]
        )


def fake_client(responses):
    completions = FakeCompletions(responses)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        completions=completions,
    )


def test_rule_fallback_extracts_separate_risks_without_evidence() -> None:
    result = TextAnalyzer().analyze(
        "权威机构验证，7天淡纹80%，敏感肌绝对不会过敏。",
        [],
    )

    assert len(result.claims) >= 3
    assert all(
        claim.verification_status != "contradicted"
        for claim in result.claims
    )
    assert {finding.category for finding in result.findings} >= {
        "fake_endorsement_risk",
        "exaggerated_claim",
        "unsupported_claim",
    }
    assert any(finding.verdict == "insufficient_evidence" for finding in result.findings)
    numeric_claim = next(claim for claim in result.claims if "80%" in claim.text)
    assert numeric_claim.duration == "7天"
    assert numeric_claim.metric == "淡纹"
    assert numeric_claim.value == "80%"


def test_optional_ai_text_signal_is_low_severity_provenance_only() -> None:
    class Detector:
        def detect_ai_text(self, text):
            from content_trust.schemas import AITextSignal

            return AITextSignal(
                available=True,
                provider="test_detector",
                ai_fraction=0.92,
                human_fraction=0.08,
                score=0.92,
            )

    result = TextAnalyzer(ai_text_detector=Detector()).analyze(
        "水的化学式是H2O。", []
    )

    finding = next(
        item for item in result.findings if item.category == "ai_generated_text"
    )
    assert finding.severity == "low"
    assert "does not establish" in finding.reason


def test_ai_text_detection_is_unavailable_without_a_dedicated_provider() -> None:
    result = TextAnalyzer().analyze("水的化学式是H2O。", [])

    assert result.ai_text_signal is not None
    assert result.ai_text_signal.available is False
    assert result.ai_text_signal.score is None
    assert result.ai_text_signal.reason == "provider_not_configured"
    assert not any(f.category == "ai_generated_text" for f in result.findings)


def test_product_identity_without_evidence_stays_unverified() -> None:
    result = TextAnalyzer().analyze("这款精华是 X Serum。", [])

    product_claim = next(
        claim for claim in result.claims if claim.claim_type == "product_identity"
    )
    assert product_claim.verification_status == "insufficient_evidence"
    assert product_claim.evidence_ids == []


def test_subjective_seeding_copy_does_not_create_integrity_finding() -> None:
    result = TextAnalyzer().analyze(
        "我混油皮，夏天用这款精华觉得清爽，香味也比较淡，日常保湿够用。",
        [],
    )

    assert result.text_integrity.status == "no_clear_signal"
    assert result.text_integrity.signals == []
    assert not any(f.category == "semantic_anomaly" for f in result.findings)
    assert result.semantic_anomaly_score is None


def test_exact_repeated_sentence_is_located_as_weak_rule_signal() -> None:
    sentence = "这款面霜推开很轻薄，早上出门用起来很方便。"
    text = sentence + sentence

    result = TextAnalyzer().analyze(text, [])

    signal = result.text_integrity.signals[0]
    assert signal.signal_type == "repetition"
    assert signal.source == "rule"
    assert signal.strength == "weak"
    assert text[signal.start_char : signal.end_char] == signal.span
    assert text[signal.related_start_char : signal.related_end_char] == signal.related_span
    assert result.findings[-1].category == "semantic_anomaly"
    assert result.findings[-1].verdict == "unknown"
    assert result.semantic_anomaly_score is None


def test_model_text_integrity_requires_exact_spans_and_does_not_set_ai_label() -> None:
    class Claims:
        def analyze_claims(self, text, evidence_items):
            return TextAnalysisResult(
                text_integrity=TextIntegrityAnalysis(
                    signals=[
                        TextIntegritySignal(
                            id="style-1",
                            signal_type="style_shift",
                            span="我最近早上薄涂，推开很快。",
                            related_span="经皮肤测试，角质层水分统计学显著改善。",
                            observation="前后句段的表达风格差异明显。",
                            reason="一处是个人口语体验，另一处是正式测试表述。",
                            benign_alternative="可能引用了品牌资料。",
                            strength="weak",
                        ),
                        TextIntegritySignal(
                            id="invalid-span",
                            signal_type="abrupt_transition",
                            span="模型改写后并不存在的句子",
                            related_span="经皮肤测试，角质层水分统计学显著改善。",
                            observation="无效引用。",
                            reason="无效引用。",
                            benign_alternative="无。",
                        ),
                    ]
                )
            )

    text = "我最近早上薄涂，推开很快。经皮肤测试，角质层水分统计学显著改善。"
    result = TextAnalyzer(claim_provider=Claims()).analyze(text, [])

    assert result.text_integrity.model_reviewed is True
    assert len(result.text_integrity.signals) == 1
    signal = result.text_integrity.signals[0]
    assert signal.source == "llm"
    assert text[signal.start_char : signal.end_char] == signal.span
    assert text[signal.related_start_char : signal.related_end_char] == signal.related_span
    assert result.ai_text_signal.available is False
    assert not any(f.category == "ai_generated_text" for f in result.findings)
    assert result.semantic_anomaly_score is None


def test_deepseek_validates_claim_output_with_pydantic() -> None:
    payload = TextAnalysisResult(
        claims=[
            {
                "id": "claim-1",
                "text": "7天淡纹80%",
                "claim_type": "efficacy",
                "verification_status": "insufficient_evidence",
                "risk_score": 0.6,
                "confidence": None,
                "evidence_ids": [],
                "observation": "文案声称7天淡纹80%",
                "reason": "没有对应证据",
            }
        ]
    )
    sdk = fake_client([payload.model_dump_json()])
    client = DeepSeekClient(Settings(), client=sdk)

    result = client.analyze_claims("7天淡纹80%", [])

    assert result.claims[0].verification_status == "insufficient_evidence"
    assert sdk.completions.calls == 1
    assert "text_integrity" in sdk.completions.requests[0]["messages"][0]["content"]


def test_deepseek_retries_invalid_json_once_then_accepts_valid_response() -> None:
    valid_payload = TextAnalysisResult().model_dump_json()
    sdk = fake_client(["not-json", valid_payload])
    client = DeepSeekClient(Settings(), client=sdk)

    result = client.analyze_claims("普通文案", [])

    assert result.claims == []
    assert sdk.completions.calls == 2


def test_deepseek_stops_after_one_schema_validation_retry() -> None:
    sdk = fake_client(['{"unexpected":"field"}', '{"unexpected":"field"}'])
    client = DeepSeekClient(Settings(), client=sdk)

    with pytest.raises(ProviderError) as exc_info:
        client.analyze_claims("普通文案", [])

    assert exc_info.value.code == "schema_validation_failed"
    assert sdk.completions.calls == 2


def test_claim_request_contains_only_supplied_evidence() -> None:
    sdk = fake_client([TextAnalysisResult().model_dump_json()])
    client = DeepSeekClient(Settings(), client=sdk)
    item = EvidenceItem(id="official-1", source="官方报告", text="4周改善18%")

    client.analyze_claims("7天淡纹80%", [item])

    user_content = sdk.completions.requests[0]["messages"][1]["content"]

    assert sdk.completions.calls == 1
    assert '"id": "official-1"' in user_content
    assert "4周改善18%" in user_content


def test_deepseek_vision_uses_local_image_and_structured_result(tmp_path) -> None:
    from content_trust.schemas import CrossModalResult

    image = tmp_path / "product.jpg"
    image.write_bytes(b"\xff\xd8\xffimage")
    sdk = fake_client([CrossModalResult(status="NOT_VERIFIABLE").model_dump_json()])
    client = DeepSeekClient(Settings(), client=sdk)

    result = client.analyze_consistency(
        image_path=str(image),
        text="图片展示了使用7天后的效果。",
        claims=[],
    )

    image_part = sdk.completions.requests[0]["messages"][1]["content"][1]
    assert result.status == "NOT_VERIFIABLE"
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_deepseek_timeout_retries_once() -> None:
    sdk = fake_client([TimeoutError("timeout"), TimeoutError("timeout")])
    client = DeepSeekClient(Settings(), client=sdk)

    with pytest.raises(ProviderError) as exc_info:
        client.analyze_claims("普通文案", [])

    assert exc_info.value.code == "provider_timeout"
    assert sdk.completions.calls == 2
