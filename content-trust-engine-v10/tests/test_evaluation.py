import json

from content_trust.config import Settings
from content_trust.evaluation.runner import run_evaluation
from content_trust.service import ContentTrustEngine


def test_jsonl_evaluation_reports_metrics_and_cases(tmp_path) -> None:
    cases = [
        {
            "id": "normal",
            "text": "这款面霜质地清爽。",
            "expected_categories": [],
        },
        {
            "id": "absolute",
            "text": "100%有效，绝对不过敏。",
            "expected_categories": ["exaggerated_claim"],
        },
    ]
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )

    result = run_evaluation(dataset, engine=ContentTrustEngine(Settings()))

    assert result["total_cases"] == 2
    assert result["json_valid_rate"] == 1.0
    assert result["category_hit_rate"] == 1.0
    assert result["false_positive_cases"] == []
    assert result["explanation_completeness"] == 1.0
    assert result["review_priority_distribution"]["review"] >= 1


def test_invalid_input_line_is_recorded_separately_from_report_json_rate(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"id":"ok","text":"普通文案","expected_categories":[]}\n'
        '{"text":"missing id"}\n',
        encoding="utf-8",
    )

    result = run_evaluation(dataset, engine=ContentTrustEngine(Settings()))

    assert result["total_cases"] == 2
    assert result["evaluated_cases"] == 1
    assert result["input_jsonl_valid_rate"] == 0.5
    assert result["json_valid_rate"] == 1.0
    assert result["failed_cases"][0]["id"] == "line-2"


def test_provider_dependent_cases_are_skipped_in_offline_evaluation(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"id":"requires-deepseek","text":"sample","expected_categories":["contradicted_claim"],"requires_providers":["deepseek"]}\n',
        encoding="utf-8",
    )

    result = run_evaluation(dataset, engine=ContentTrustEngine(Settings()))

    assert result["total_cases"] == 1
    assert result["evaluated_cases"] == 0
    assert result["json_valid_rate"] == 1.0
    assert result["skipped_cases"] == [
        {"id": "requires-deepseek", "reason": "requires providers: deepseek"}
    ]
