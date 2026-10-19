import pytest
from pytest import approx

from content_trust.evaluation.image_metrics import (
    evaluate_image_scores,
    validate_group_splits,
)


def _row(image_id, split, label, score, group):
    return {
        "id": image_id,
        "target": "ai_generated",
        "split": split,
        "label": label,
        "score": score,
        "evidence_present": score is not None,
        "group_ids": {
            "post_id": f"post-{group}",
            "creator_id": f"creator-{group}",
            "source_id": f"source-{group}",
            "generation_pipeline_id": f"pipeline-{group}",
        },
    }


def test_group_split_rejects_creator_leakage() -> None:
    rows = [
        _row("a", "calibration", 0, 0.1, "same"),
        _row("b", "test", 1, 0.9, "same"),
    ]

    errors = validate_group_splits(rows)

    assert any("creator_id" in error for error in errors)
    with pytest.raises(ValueError, match="Invalid group split"):
        evaluate_image_scores(rows, target="ai_generated")


def test_metrics_use_calibration_threshold_and_report_abstention() -> None:
    rows = [
        _row("cal-neg-low", "calibration", 0, 0.1, "c1"),
        _row("cal-neg-high", "calibration", 0, 0.4, "c2"),
        _row("cal-pos", "calibration", 1, 0.9, "c3"),
        _row("test-neg", "test", 0, 0.2, "t1"),
        _row("test-pos", "test", 1, 0.8, "t2"),
        _row("test-abstain", "test", 0, None, "t3"),
    ]

    result = evaluate_image_scores(
        rows,
        target="ai_generated",
        target_fpr=0.0,
    )

    assert result["score_coverage"] == approx(5 / 6)
    assert result["evidence_coverage"] == approx(5 / 6)
    assert result["abstention_rate"] == approx(1 / 6, abs=1e-6)
    assert result["decision_threshold"]["threshold_from_calibration_split"] >= 0.9
    assert result["test_at_calibrated_threshold"]["false_accusation_rate"] == 0.0
    assert result["test_at_calibrated_threshold"]["tpr_at_target_fpr"] == 0.0
    assert result["test_raw_score_calibration_diagnostics"]["interpretation"].startswith(
        "Diagnostic only"
    )


def test_ranking_metrics_return_perfect_ordering() -> None:
    rows = [
        _row("cal-neg", "calibration", 0, 0.1, "c1"),
        _row("cal-pos", "calibration", 1, 0.9, "c2"),
        _row("test-neg", "test", 0, 0.2, "t1"),
        _row("test-pos", "test", 1, 0.8, "t2"),
    ]

    result = evaluate_image_scores(rows, target="ai_generated")

    assert result["test_ranking_metrics"]["auroc"] == 1.0
    assert result["test_ranking_metrics"]["auprc"] == 1.0
