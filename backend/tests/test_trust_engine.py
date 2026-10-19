"""Trust-engine integration tests.

Two layers:
  * **Pure mapping** — always runs; proves the engine's output shape lands
    correctly on our frozen ``AgentOutput`` contract.
  * **Live engine** — skipped automatically when ``content_trust`` is not
    installed, so the backend test suite stays green without the optional
    package.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.enums import RiskLevel
from app.services.trust_engine_runner import (
    ENGINE_TO_OURS,
    _findings_by_dimension,
    _to_agent_output,
)

# -- pure mapping ------------------------------------------------------------


def test_dimension_name_mapping_is_stable():
    """The engine's role names are now identical to our public dimension keys."""
    assert ENGINE_TO_OURS == {
        "text": "text",
        "comment": "comment",
        "image": "image",
        "account": "account",
    }


def test_findings_by_dimension_splits_text_image_and_comment_clusters():
    report = {
        "text_analysis": {
            "findings": [
                {
                    "id": "tf-1",
                    "category": "exaggerated_claim",
                    "observation": "7天白一个度",
                    "reason": "功效夸大",
                    "severity": "medium",
                    "risk_score": 0.5,
                }
            ]
        },
        "images": [
            {
                "findings": [
                    {
                        "id": "if-1",
                        "category": "ai_generated_image",
                        "observation": "疑似生成",
                        "reason": "线索",
                        "severity": "high",
                        "risk_score": 0.7,
                    }
                ]
            }
        ],
        "comment_analysis": {
            "pattern_clusters": [
                {"pattern_type": "sampled_unique_comments", "verdict": "uncertain", "representative_text": "x"},
                {
                    "pattern_type": "repeated_template",
                    "verdict": "suspicious",
                    "representative_text": "好用",
                    "reason": "重复模板",
                    "recommended_action": "核查账号",
                },
            ]
        },
    }

    by_dimension = _findings_by_dimension(report)

    assert [f["category"] for f in by_dimension["text"]] == ["exaggerated_claim"]
    assert [f["category"] for f in by_dimension["image"]] == ["ai_generated_image"]
    # The engine's "sampled, not a signal" bucket is deliberately dropped.
    assert [f["category"] for f in by_dimension["comment"]] == ["repeated_template"]
    assert by_dimension["comment"][0]["severity"] == "high"
    assert by_dimension["comment"][0]["recommended_action"] == "核查账号"


def test_findings_by_dimension_handles_single_image_report():
    """TrustReport (one image) keeps image findings in the top-level list."""
    report = {
        "text_analysis": {"findings": [{"id": "tf-1", "category": "unsupported_claim"}]},
        "image_analysis": {"available": True},
        "findings": [
            {"id": "tf-1", "category": "unsupported_claim"},
            {"id": "if-1", "category": "ai_generated_image"},
        ],
    }
    by_dimension = _findings_by_dimension(report)
    assert [f["id"] for f in by_dimension["image"]] == ["if-1"]


def test_to_agent_output_maps_assessed_dimension():
    output = _to_agent_output(
        our_name="text",
        engine_dimension="text",
        assessment={
            "dimension": "text",
            "status": "assessed",
            "score": 88.0,
            "risk_level": "high",
            "summary": "文案维度风险分 88.0/100",
            "evidence": ["7天白一个度"],
            "limitations": ["证据不足不等于虚假。"],
        },
        findings=[
            {
                "category": "exaggerated_claim",
                "observation": "7天白一个度",
                "reason": "功效夸大",
                "severity": "high",
                "risk_score": 0.8,
                "recommended_action": "收敛功效承诺",
            }
        ],
        role={
            "role": "text",
            "provider": "deepseek",
            "model": "deepseek-flash",
            "prompt_version": "v10",
            "status": "fallback",
        },
        limitations=["总风险分是内部聚合，不是真假概率。"],
    )

    assert output.agent == "text"
    assert output.risk_level is RiskLevel.HIGH
    assert output.score == 88.0
    assert output.findings[0].type == "exaggerated_claim"
    assert output.findings[0].confidence == 0.8
    assert output.recommendations == ["收敛功效承诺"]
    assert output.meta["backend"] == "trust_engine"
    assert output.meta["role_status"] == "fallback"
    assert output.meta["prompt_version"] == "v10"
    assert output.meta["limitations"] == ["总风险分是内部聚合，不是真假概率。"]
    assert output.meta["dimension_limitations"] == ["证据不足不等于虚假。"]


def test_to_agent_output_keeps_unavailable_dimension_honest():
    output = _to_agent_output(
        our_name="image",
        engine_dimension="image",
        assessment={
            "dimension": "image",
            "status": "unavailable",
            "score": None,
            "risk_level": None,
            "summary": "Qwen 视觉分析不可用。",
        },
        findings=[],
        role={"role": "image", "provider": "qwen_vision", "status": "unavailable", "error_code": "provider_not_configured"},
        limitations=[],
    )
    assert output.score == 0.0
    assert output.risk_level is RiskLevel.LOW
    assert output.findings == []
    assert "不可用" in output.summary
    assert output.meta["provider_error"] == "provider_not_configured"


def test_to_agent_output_falls_back_to_evidence_when_no_structured_findings():
    output = _to_agent_output(
        our_name="comment",
        engine_dimension="comment",
        assessment={
            "dimension": "comment",
            "status": "assessed",
            "score": 20.0,
            "risk_level": "low",
            "summary": "评论维度风险分 20.0/100",
            "evidence": ["已覆盖 9 条评论"],
        },
        findings=[],
        role=None,
        limitations=[],
    )
    assert output.findings[0].type == "engine_evidence"
    assert output.findings[0].evidence == "已覆盖 9 条评论"


# -- live engine (optional dependency) ---------------------------------------


def test_runner_maps_engine_output_offline():
    pytest.importorskip("content_trust")

    from app.core.config import get_settings
    from app.models.content_sample import ContentSample
    from app.services.trust_engine_runner import TrustEngineRunner

    runner = TrustEngineRunner.from_settings(get_settings())
    assert runner.available is True

    sample = ContentSample(
        id="sample-runner-test",
        text_content="姐妹们！这支精华100%修复敏感肌，7天白一个度，皮肤科医生推荐。",
        text_length=31,
        comment_payload=[
            {"index": 0, "content": "好用", "author": "u1", "likes": 0, "created_at": None}
        ],
        comment_count=1,
        source="test",
    )

    run = asyncio.run(runner.run(task_id="task-runner-test", sample=sample))

    assert run.available is True, run.error
    assert set(run.dimension_outputs) == {"text", "comment", "image", "account"}
    assert run.dimension_outputs["text"].agent == "text"
    assert 0.0 <= run.dimension_outputs["text"].score <= 100.0
    assert isinstance(run.dimension_outputs["text"].risk_level, RiskLevel)

    trace = run.trace
    assert trace is not None
    assert trace["backend"] == "trust_engine"
    assert len(trace["visible_roles"]) == 4
    assert trace["evidence_chain"]
    assert trace["limitations"]


def test_runner_reports_unavailable_when_engine_import_fails(monkeypatch):
    """A missing package must degrade, never raise."""
    import app.services.trust_engine_runner as runner_module

    monkeypatch.setattr(runner_module, "ENGINE_AVAILABLE", False)
    monkeypatch.setattr(runner_module, "_IMPORT_ERROR", "simulated missing package")

    from app.core.config import get_settings

    runner = runner_module.TrustEngineRunner.from_settings(get_settings())
    assert runner.available is False
