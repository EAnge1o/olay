"""Run the current image pipeline on one representative image per test post."""

from __future__ import annotations

import json
import os
import re
import statistics
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from content_trust.config import Settings
from content_trust.analyzers.provenance import ProvenanceAnalyzer
from content_trust.schemas import TrustInput
from content_trust.service import ContentTrustEngine


ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = (
    ROOT
    / "data"
    / "model-experiments"
    / "trufor_detectivesam_test_package_3_20261015"
    / "dataset"
    / "测试数据包"
)
BASELINE_PATH = (
    ROOT
    / "data"
    / "model-experiments"
    / "trufor_detectivesam_test_package_3_20261015"
    / "summary.json"
)
OUTPUT_DIR = ROOT / "data" / "model-experiments" / "current_engine_trufor_c2pa_test_package3_20261016"


def _image_number(path: Path) -> int:
    match = re.search(r"image_(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def _auc(scores: list[float], labels: list[bool]) -> float | None:
    positives = [score for score, label in zip(scores, labels) if label]
    negatives = [score for score, label in zip(scores, labels) if not label]
    if not positives or not negatives:
        return None
    wins = sum(
        (positive > negative) + 0.5 * (positive == negative)
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def main() -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/content-trust-mpl")
    os.environ.setdefault("TRUFOR_ARTIFACT_DIR", str(OUTPUT_DIR / "artifacts"))
    if not DATASET_ROOT.is_dir() or not BASELINE_PATH.is_file():
        raise SystemExit("The prior test-package dataset or baseline summary is missing.")

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_by_path = {
        str(Path(row["image_path"]).resolve()): row
        for row in baseline["image_results"]
    }
    post_labels = baseline["post_labels"]
    image_paths = sorted(
        (
            path
            for path in DATASET_ROOT.glob("sample_*/post/*")
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ),
        key=lambda path: (path.parent.parent.name, _image_number(path)),
    )
    first_image_per_post: dict[str, Path] = {}
    for path in image_paths:
        first_image_per_post.setdefault(path.parent.parent.name, path)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "artifacts").mkdir(exist_ok=True)

    settings = replace(
        Settings.from_env(),
        deepseek_api_key=None,
        trufor_enabled=True,
    )
    engine = ContentTrustEngine(settings=settings)
    results_path = OUTPUT_DIR / "results.jsonl"
    results: list[dict] = []
    with results_path.open("w", encoding="utf-8") as output:
        for index, (post_id, image_path) in enumerate(
            first_image_per_post.items(), start=1
        ):
            started = time.perf_counter()
            report = engine.analyze(
                TrustInput(content_id=post_id, image_path=str(image_path.resolve()))
            )
            serialized = report.model_dump(mode="json")
            image = serialized.get("image_analysis") or {}
            provenance = serialized.get("provenance_analysis") or {}
            generation = image.get("ai_generation_signal") or {}
            trufor = image.get("trufor_analysis") or {}
            baseline_row = baseline_by_path.get(str(image_path.resolve()), {})
            baseline_trufor = baseline_row.get("trufor") or {}
            record = {
                "post_id": post_id,
                "post_is_fake": post_labels.get(post_id),
                "image_path": str(image_path.resolve()),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "review_priority": serialized.get("review_priority"),
                "lota": generation,
                "nfa_localization_score": image.get("localized_manipulation_score"),
                "nfa_status": image.get("nfa_status"),
                "trufor": trufor,
                "provenance": provenance,
                "provider_errors": image.get("provider_errors", []),
                "finding_categories": sorted(
                    {finding["category"] for finding in serialized.get("findings", [])}
                ),
                "baseline_trufor": baseline_trufor,
            }
            results.append(record)
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            print(
                f"[{index}/{len(first_image_per_post)}] {post_id}: "
                f"LOTA={generation.get('score')} "
                f"NFA={record['nfa_localization_score']} "
                f"TruFor={trufor.get('detection_score')} "
                f"C2PA={provenance.get('status')} "
                f"EXIF={provenance.get('exif_present')} "
                f"elapsed={record['elapsed_ms'] / 1000:.1f}s",
                flush=True,
            )

    # Provenance is cheap and independent of the slower forensic models, so check
    # every image in the package rather than only the representative subset.
    provenance_analyzer = ProvenanceAnalyzer()
    provenance_rows = [
        provenance_analyzer.analyze(image_path=str(path.resolve())).model_dump(mode="json")
        for path in image_paths
    ]
    provenance_summary = {
        "image_count": len(provenance_rows),
        "c2pa_status_counts": dict(Counter(row["status"] for row in provenance_rows)),
        "manifest_present_count": sum(row["manifest_present"] is True for row in provenance_rows),
        "exif_available_count": sum(row["exif_available"] is True for row in provenance_rows),
        "exif_present_count": sum(row["exif_present"] is True for row in provenance_rows),
        "exif_error_counts": dict(Counter(row["exif_error"] for row in provenance_rows if row["exif_error"])),
    }
    (OUTPUT_DIR / "provenance_all_images.json").write_text(
        json.dumps(provenance_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    current_scores = [
        row["trufor"]["detection_score"]
        for row in results
        if row["trufor"].get("detection_score") is not None
    ]
    labels = [
        row["post_is_fake"]
        for row in results
        if row["trufor"].get("detection_score") is not None
    ]
    current_localization = [
        row["trufor"]["localization_score"]
        for row in results
        if row["trufor"].get("localization_score") is not None
    ]
    old_detection = [
        row["baseline_trufor"]["raw_detection_score"]
        for row in results
        if row["trufor"].get("detection_score") is not None
    ]
    old_localization = [
        row["baseline_trufor"]["localization_top_1_percent_mean"]
        for row in results
        if row["trufor"].get("localization_score") is not None
    ]
    real_trufor_scores = [
        row["trufor"]["detection_score"]
        for row in results
        if not row["post_is_fake"] and row["trufor"].get("detection_score") is not None
    ]
    fake_trufor_scores = [
        row["trufor"]["detection_score"]
        for row in results
        if row["post_is_fake"] and row["trufor"].get("detection_score") is not None
    ]
    real_nfa_scores = [
        row["nfa_localization_score"]
        for row in results
        if not row["post_is_fake"] and row["nfa_localization_score"] is not None
    ]
    fake_nfa_scores = [
        row["nfa_localization_score"]
        for row in results
        if row["post_is_fake"] and row["nfa_localization_score"] is not None
    ]
    summary = {
        "experiment": "Current ContentTrustEngine with LOTA + NFA-ViT + TruFor + C2PA/EXIF",
        "status": "exploratory_one_image_per_post_no_image_level_ground_truth",
        "dataset_post_count": len(first_image_per_post),
        "dataset_image_count": len(image_paths),
        "sample_policy": "first image by numeric image_N order from each post",
        "semantic_llm_calls": 0,
        "trufor_device": "cpu",
        "sample_trufor_post_label_auc": _auc(current_scores, labels),
        "sample_trufor_detection_mean_absolute_delta_vs_prior_same_images": (
            statistics.mean(abs(new - old) for new, old in zip(current_scores, old_detection))
            if current_scores and len(current_scores) == len(old_detection)
            else None
        ),
        "sample_trufor_localization_mean_absolute_delta_vs_prior_same_images": (
            statistics.mean(abs(new - old) for new, old in zip(current_localization, old_localization))
            if current_localization and len(current_localization) == len(old_localization)
            else None
        ),
        "sample_trufor_detection_mean_real": (
            statistics.mean(real_trufor_scores) if real_trufor_scores else None
        ),
        "sample_trufor_detection_mean_fake": (
            statistics.mean(fake_trufor_scores) if fake_trufor_scores else None
        ),
        "nfa_sample_mean_real": statistics.mean(real_nfa_scores) if real_nfa_scores else None,
        "nfa_sample_mean_fake": statistics.mean(fake_nfa_scores) if fake_nfa_scores else None,
        "provenance_all_images": provenance_summary,
        "limitations": [
            "The package labels posts, not individual images or edited pixels.",
            "One first image per post is a small exploratory sample; its AUC is not directly comparable to the prior all-image post aggregation.",
            "LOTA and TruFor values are uncalibrated signals; NFA is a localization hint, not an AI-generation probability.",
            "Missing C2PA and EXIF metadata are neutral and do not imply manipulation.",
        ],
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved {OUTPUT_DIR / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
