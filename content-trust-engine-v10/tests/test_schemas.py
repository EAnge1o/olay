import json

import pytest
from pydantic import ValidationError

from content_trust import (
    AITextSignal,
    EvidenceItem,
    Finding,
    ImageForensicsResult,
    TrustInput,
    TrustReport,
)


def test_trust_input_accepts_text_image_and_evidence() -> None:
    request = TrustInput(
        content_id="demo-1",
        text="7 天淡纹 80%",
        image_url="https://example.test/serum.jpg",
        evidence_items=[
            EvidenceItem(id="ev-1", source="product study", text="4 周改善 18%")
        ],
    )

    assert request.evidence_items[0].id == "ev-1"
    assert request.locale == "zh-CN"


def test_trust_input_rejects_two_image_sources() -> None:
    with pytest.raises(ValidationError, match="at most one"):
        TrustInput(content_id="demo-1", image_path="./a.jpg", image_url="https://x.test/a.jpg")


def test_trust_input_rejects_empty_content() -> None:
    with pytest.raises(ValidationError, match="Provide at least one"):
        TrustInput(content_id="demo-empty")


def test_ai_text_signal_supports_unconfigured_provider() -> None:
    signal = AITextSignal(
        available=False,
        score=None,
        reason="provider_not_configured",
    )

    assert signal.model_dump(mode="json") == {
        "available": False,
        "score": None,
        "ai_fraction": None,
        "human_fraction": None,
        "confidence": None,
        "provider": None,
        "reason": "provider_not_configured",
    }


def test_scores_must_be_in_unit_interval() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="f-1",
            category="exaggerated_claim",
            verdict="suspicious",
            severity="high",
            risk_score=1.2,
            observation="绝对不会过敏",
            reason="绝对化安全承诺",
            source="rule",
            recommended_action="人工复核",
        )


def test_contradicted_finding_requires_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="must cite evidence_ids"):
        Finding(
            id="f-contradicted",
            category="contradicted_claim",
            verdict="contradicted",
            severity="high",
            risk_score=0.9,
            observation="文案写7天改善80%。",
            reason="没有引用资料的系统不得下矛盾结论。",
            source="llm",
            recommended_action="补充资料。",
        )


def test_report_json_serialization_hides_raw_provider_payload() -> None:
    forensic = ImageForensicsResult(
        provider="nfa_vit_space",
        localized_manipulation_score=0.91,
        available=True,
        raw_response={"vendor_secret_field": "internal-only"},
    )
    report = TrustReport(
        content_id="demo-2",
        review_priority="review",
        image_analysis=forensic,
        summary="检测到较强的 AI 图像信号。",
    )

    payload = report.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "vendor_secret_field" not in serialized
    assert payload["image_analysis"]["localized_manipulation_score"] == 0.91
