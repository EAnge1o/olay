from types import SimpleNamespace

from app.services.presentation import build_dimension_reads


def _row(**overrides):
    values = {
        "agent_name": "text",
        "assessed": True,
        "score": 80.0,
        "risk_level": "high",
        "status": "completed",
        "summary": "文案存在绝对化功效表述。",
        "findings": [],
        "raw_output": {
            "engine_status": "assessed",
            "dimension_limitations": ["文案维度专属局限。"],
            "limitations": ["报告级全局局限。"],
        },
        "duration_ms": 10,
        "error_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_dimension_reads_keep_specific_limitations_only():
    reads = build_dimension_reads([_row()])
    text = next(item for item in reads if item.key == "text")

    assert text.limitations == ["文案维度专属局限。"]


def test_unassessed_dimension_does_not_inherit_report_limitations():
    reads = build_dimension_reads(
        [
            _row(
                agent_name="image",
                assessed=False,
                score=0.0,
                risk_level=None,
                summary="图片维度暂不评分。",
                raw_output={
                    "engine_status": "not_applicable",
                    "limitations": ["报告级全局局限。"],
                },
            )
        ]
    )
    image = next(item for item in reads if item.key == "image")

    assert image.limitations == []
