"""Metric aggregation for JSONL evaluation runs."""

from typing import Any


def summarize_results(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated_cases = [
        case
        for case in case_results
        if not case.get("input_error") and not case.get("skipped")
    ]
    loaded_cases = [case for case in case_results if not case.get("input_error")]
    attempted = len(evaluated_cases)
    valid_reports = [case for case in evaluated_cases if case["json_valid"]]
    expected_total = sum(len(case["expected_categories"]) for case in evaluated_cases)
    expected_hits = sum(len(case["matched_categories"]) for case in evaluated_cases)
    expected_integrity_total = sum(
        len(case.get("expected_text_integrity_signals", []))
        for case in evaluated_cases
    )
    integrity_hits = sum(
        len(case.get("matched_text_integrity_signals", []))
        for case in evaluated_cases
    )
    integrity_status_cases = [
        case
        for case in evaluated_cases
        if case.get("expected_text_integrity_status") is not None
        and case.get("predicted_text_integrity_status") is not None
    ]
    false_positive_cases = [
        case["id"]
        for case in evaluated_cases
        if not case["expected_categories"] and case["predicted_categories"]
    ]
    complete_explanations = sum(
        1 for case in valid_reports if case["explanation_complete"]
    )
    provider_failures = sum(len(case["provider_errors"]) for case in evaluated_cases)
    latency_values = [case["latency_ms"] for case in evaluated_cases]
    priority_counts = {key: 0 for key in ("low", "review", "high")}
    for case in valid_reports:
        priority = case.get("predicted_review_priority")
        if priority in priority_counts:
            priority_counts[priority] += 1
    priority_cases = [
        case
        for case in valid_reports
        if case.get("review_priority_match") is not None
    ]

    return {
        "total_cases": len(case_results),
        "evaluated_cases": attempted,
        "skipped_cases": [
            {"id": case["id"], "reason": case["skip_reason"]}
            for case in case_results
            if case.get("skipped")
        ],
        "input_jsonl_valid_rate": _ratio(len(loaded_cases), len(case_results)),
        "json_valid_rate": _ratio(len(valid_reports), attempted),
        "category_hit_rate": _ratio(expected_hits, expected_total),
        "false_positive_cases": false_positive_cases,
        "false_accusation_cases": false_positive_cases,
        "text_integrity_signal_hit_rate": _ratio(
            integrity_hits, expected_integrity_total
        ),
        "false_positive_text_integrity_cases": [
            case["id"]
            for case in evaluated_cases
            if case.get("false_positive_text_integrity")
        ],
        "text_integrity_status_accuracy": _ratio(
            sum(
                case["text_integrity_status_match"] is True
                for case in integrity_status_cases
            ),
            len(integrity_status_cases),
        ),
        "failed_api_calls": provider_failures,
        "average_latency_ms": round(sum(latency_values) / attempted, 2)
        if attempted
        else 0.0,
        "review_priority_distribution": priority_counts,
        "review_priority_accuracy": _ratio(
            sum(case["review_priority_match"] is True for case in priority_cases),
            len(priority_cases),
        ),
        "explanation_completeness": _ratio(complete_explanations, len(valid_reports)),
        "category_misses": [
            {"id": case["id"], "categories": case["missed_categories"]}
            for case in evaluated_cases
            if case["missed_categories"]
        ],
        "failed_cases": [
            {"id": case["id"], "error": case["error"]}
            for case in case_results
            if case["error"] or case.get("input_error")
        ],
        "cases": case_results,
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 4)
