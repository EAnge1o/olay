"""Beauty-domain image detector metrics with creator/source group safeguards.

Scores stay raw model signals. These diagnostics do not turn an uncalibrated score
into a probability; thresholds are selected on the calibration split only.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

_SPLITS = {"train", "calibration", "test"}
_GROUP_KEYS = ("post_id", "creator_id", "source_id", "generation_pipeline_id")


def validate_group_splits(records: list[dict[str, Any]]) -> list[str]:
    """Return leakage errors when a group appears in more than one split."""

    errors: list[str] = []
    group_splits: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    ids: set[str] = set()
    for index, row in enumerate(records, start=1):
        if not isinstance(row, dict):
            errors.append(f"line-{index}: row must be an object")
            continue
        raw_id = row.get("id")
        row_id = str(raw_id or f"line-{index}")
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append(f"{row_id}: id is required")
        split = row.get("split")
        if not isinstance(split, str) or split not in _SPLITS:
            errors.append(f"{row_id}: invalid split {split!r}")
        if isinstance(raw_id, str) and raw_id in ids:
            errors.append(f"{row_id}: duplicate id")
        if isinstance(raw_id, str):
            ids.add(raw_id)
        groups = row.get("group_ids") or {}
        if not isinstance(groups, dict):
            errors.append(f"{row_id}: group_ids must be an object")
            continue
        populated = False
        for key in _GROUP_KEYS:
            value = groups.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                errors.append(f"{row_id}: {key} must be a string or null")
                continue
            if not value.strip():
                continue
            populated = True
            if split in _SPLITS:
                group_splits[key][str(value)].add(split)
        if not populated:
            errors.append(f"{row_id}: at least one group id is required")

    for key, values in group_splits.items():
        for value, splits in values.items():
            if len(splits) > 1:
                errors.append(
                    f"{key}={value!r} occurs across splits: {', '.join(sorted(splits))}"
                )
    return errors


def evaluate_image_scores(
    records: list[dict[str, Any]],
    *,
    target: str,
    target_fpr: float = 0.05,
    ece_bins: int = 10,
) -> dict[str, Any]:
    """Evaluate one binary target (e.g. ``ai_generated`` or ``manipulation``).

    Every row must have an id, split, target label and group_ids. ``score`` may be
    null to represent abstention/provider unavailability. Scores must be in [0,1].
    """

    if (
        not isinstance(target_fpr, (int, float))
        or not math.isfinite(float(target_fpr))
        or not 0 <= target_fpr <= 1
    ):
        raise ValueError("target_fpr must be in [0, 1]")
    if ece_bins < 1:
        raise ValueError("ece_bins must be positive")
    leakage = validate_group_splits(records)
    if leakage:
        raise ValueError("Invalid group split:\n" + "\n".join(leakage))

    target_rows = [row for row in records if row.get("target") == target]
    if not target_rows:
        raise ValueError(f"No rows for target={target!r}")
    _validate_rows(target_rows)

    total = len(target_rows)
    scored = [row for row in target_rows if row.get("score") is not None]
    coverage = len(scored) / total if total else 0.0
    evidence_flags = [row.get("evidence_present") for row in target_rows]
    evidence_coverage = (
        sum(flag is True for flag in evidence_flags) / total
        if all(isinstance(flag, bool) for flag in evidence_flags)
        else None
    )
    by_split = {
        split: [row for row in scored if row["split"] == split]
        for split in _SPLITS
    }
    calibration = by_split["calibration"]
    test = by_split["test"]
    threshold = _threshold_for_fpr(calibration, target_fpr)
    test_metrics = _binary_metrics(test, threshold) if test else _empty_metrics()
    test_scored = by_split["test"]

    return {
        "target": target,
        "status": "experimental_unvalidated_on_beauty_images",
        "score_note": "Raw detector score; not a probability unless separately calibrated and validated.",
        "records": total,
        "scored_records": len(scored),
        "abstention_rate": round(1.0 - coverage, 6),
        "score_coverage": round(coverage, 6),
        "evidence_coverage": (
            round(evidence_coverage, 6) if evidence_coverage is not None else None
        ),
        "score_coverage_by_split": {
            split: round(
                sum(
                    row.get("score") is not None
                    for row in target_rows
                    if row["split"] == split
                )
                / max(1, sum(row["split"] == split for row in target_rows)),
                6,
            )
            for split in sorted(_SPLITS)
        },
        "group_split_errors": [],
        "split_counts": {
            split: sum(row["split"] == split for row in target_rows)
            for split in sorted(_SPLITS)
        },
        "test_ranking_metrics": _binary_metrics(test_scored, None),
        "test_raw_score_calibration_diagnostics": _calibration_metrics(
            test_scored, ece_bins
        ),
        "decision_threshold": {
            "target_fpr": target_fpr,
            "threshold_from_calibration_split": threshold,
            "calibration_negative_count": sum(row["label"] == 0 for row in calibration),
            "selection_method": "lowest threshold whose empirical calibration FPR is <= target; threshold > 1 means abstain on all",
        },
        "test_at_calibrated_threshold": {
            **test_metrics,
            "false_accusation_rate": test_metrics["false_positive_rate"],
            "tpr_at_target_fpr": test_metrics["true_positive_rate"],
        },
    }


def _validate_rows(rows: list[dict[str, Any]]) -> None:
    for index, row in enumerate(rows, start=1):
        row_id = str(row.get("id") or f"row-{index}")
        if row.get("label") not in (0, 1, False, True):
            raise ValueError(f"{row_id}: label must be 0 or 1")
        if "score" not in row:
            raise ValueError(f"{row_id}: score field is required (use null to abstain)")
        score = row["score"]
        if score is not None and (
            not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 1
        ):
            raise ValueError(f"{row_id}: score must be null or finite in [0,1]")
        if "evidence_present" in row and not isinstance(row["evidence_present"], bool):
            raise ValueError(f"{row_id}: evidence_present must be boolean when supplied")


def _threshold_for_fpr(rows: list[dict[str, Any]], target_fpr: float) -> float | None:
    negatives = [row for row in rows if row["label"] == 0]
    if not negatives:
        return None
    candidates = sorted({float(row["score"]) for row in rows}, reverse=True)
    candidates.append(math.nextafter(1.0, math.inf))
    allowed = [
        threshold
        for threshold in candidates
        if sum(float(row["score"]) >= threshold for row in negatives) / len(negatives)
        <= target_fpr
    ]
    return min(allowed) if allowed else math.nextafter(1.0, math.inf)


def _binary_metrics(rows: list[dict[str, Any]], threshold: float | None) -> dict[str, Any]:
    positives = [row for row in rows if row["label"] == 1]
    negatives = [row for row in rows if row["label"] == 0]
    if not rows or not positives or not negatives:
        return {
            "count": len(rows),
            "positive_count": len(positives),
            "negative_count": len(negatives),
            "auroc": None,
            "auprc": None,
            "threshold": threshold,
            "true_positive_rate": None,
            "false_positive_rate": None,
        }
    scores = [float(row["score"]) for row in rows]
    labels = [int(row["label"]) for row in rows]
    result: dict[str, Any] = {
        "count": len(rows),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "auroc": round(_auroc(labels, scores), 6),
        "auprc": round(_average_precision(labels, scores), 6),
        "threshold": threshold,
        "true_positive_rate": None,
        "false_positive_rate": None,
    }
    if threshold is not None:
        result["true_positive_rate"] = round(
            sum(float(row["score"]) >= threshold for row in positives) / len(positives),
            6,
        )
        result["false_positive_rate"] = round(
            sum(float(row["score"]) >= threshold for row in negatives) / len(negatives),
            6,
        )
    return result


def _empty_metrics() -> dict[str, Any]:
    return {
        "count": 0,
        "positive_count": 0,
        "negative_count": 0,
        "auroc": None,
        "auprc": None,
        "threshold": None,
        "true_positive_rate": None,
        "false_positive_rate": None,
    }


def _auroc(labels: list[int], scores: list[float]) -> float:
    positives = sum(labels)
    negatives = len(labels) - positives
    ordered = sorted(zip(scores, labels), key=lambda pair: pair[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _average_precision(labels: list[int], scores: list[float]) -> float:
    positive_total = sum(labels)
    ordered = sorted(zip(scores, labels), key=lambda pair: pair[0], reverse=True)
    true_positive = 0
    false_positive = 0
    previous_recall = 0.0
    area = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        group = ordered[index:end]
        true_positive += sum(label for _, label in group)
        false_positive += len(group) - sum(label for _, label in group)
        recall = true_positive / positive_total
        precision = true_positive / (true_positive + false_positive)
        area += (recall - previous_recall) * precision
        previous_recall = recall
        index = end
    return area


def _calibration_metrics(rows: list[dict[str, Any]], bins: int) -> dict[str, Any]:
    if not rows:
        return {"brier_score": None, "ece": None, "bins": []}
    brier = sum((float(row["score"]) - int(row["label"])) ** 2 for row in rows) / len(rows)
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(bins)]
    for row in rows:
        index = min(bins - 1, int(float(row["score"]) * bins))
        buckets[index].append(row)
    ece = 0.0
    detail = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        confidence = sum(float(row["score"]) for row in bucket) / len(bucket)
        frequency = sum(int(row["label"]) for row in bucket) / len(bucket)
        ece += len(bucket) / len(rows) * abs(confidence - frequency)
        detail.append(
            {
                "lower_bound": index / bins,
                "upper_bound": (index + 1) / bins,
                "count": len(bucket),
                "mean_score": round(confidence, 6),
                "positive_rate": round(frequency, 6),
            }
        )
    return {
        "brier_score": round(brier, 6),
        "ece": round(ece, 6),
        "bins": detail,
        "interpretation": "Diagnostic only for raw uncalibrated scores; do not describe as probability calibration.",
    }
