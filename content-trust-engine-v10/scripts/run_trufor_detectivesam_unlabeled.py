"""Run TruFor and Detective SAM on images without pixel-level masks.

This experiment compares exploratory image signals only. The package's labels are
post-level authenticity labels, so this script does not report localization IoU/F1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/ct-trufor-mpl")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from PIL import Image

from compare_trufor_detectivesam import load_trufor, trufor_predict
from detectivesam_inference.dataset import prepare_sample
from detectivesam_inference.runtime import DetectiveSAMRunner


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=ROOT
        / "data"
        / "model-experiments"
        / "trufor_detectivesam_test_package_3_20261015"
        / "dataset"
        / "测试数据包",
    )
    parser.add_argument(
        "--trufor-checkpoint",
        type=Path,
        default=ROOT / "models" / "TruFor" / "TruFor_train_test" / "pretrained_models" / "trufor.pth.tar",
    )
    parser.add_argument(
        "--detectivesam-checkpoint",
        type=Path,
        default=ROOT
        / "models"
        / "DetectiveSAM"
        / "checkpoints"
        / "model_epoch22_batch999_score1.1114.pth",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT
        / "data"
        / "model-experiments"
        / "trufor_detectivesam_test_package_3_20261015",
    )
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def read_post_label(meta_path: Path) -> bool:
    text = meta_path.read_text(encoding="utf-8")
    match = re.search(r"^is_fake:\s*(是|否)\s*$", text, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"Could not parse is_fake label from {meta_path}")
    return match.group(1) == "是"


def top_fraction_mean(values: np.ndarray, fraction: float = 0.01) -> float:
    flattened = np.asarray(values, dtype=np.float32).reshape(-1)
    count = max(1, int(np.ceil(flattened.size * fraction)))
    return float(np.partition(flattened, flattened.size - count)[-count:].mean())


def score_auc(scores: list[float], labels: list[bool]) -> float | None:
    positives = [score for score, label in zip(scores, labels) if label]
    negatives = [score for score, label in zip(scores, labels) if not label]
    if not positives or not negatives:
        return None
    wins = sum((pos > neg) + 0.5 * (pos == neg) for pos in positives for neg in negatives)
    return float(wins / (len(positives) * len(negatives)))


def overlay_panel(path: Path, image_path: Path, trufor_map: np.ndarray, dsam_map: np.ndarray) -> None:
    image = Image.open(image_path).convert("RGB")
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    axes[0].imshow(image)
    axes[0].set_title("Input image")
    for axis, heatmap, title in (
        (axes[1], trufor_map, "TruFor localization signal"),
        (axes[2], dsam_map, "Detective SAM localization signal"),
    ):
        axis.imshow(image)
        axis.imshow(heatmap, cmap="magma", vmin=0, vmax=1, alpha=0.55)
        axis.set_title(title)
    for axis in axes:
        axis.axis("off")
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    dataset_root = args.dataset_root.resolve()
    output_dir = args.output_dir.resolve()
    map_dir = output_dir / "maps"
    visualization_dir = output_dir / "visualizations"
    map_dir.mkdir(parents=True, exist_ok=True)
    visualization_dir.mkdir(parents=True, exist_ok=True)

    for checkpoint in (args.trufor_checkpoint, args.detectivesam_checkpoint):
        if not checkpoint.is_file():
            raise FileNotFoundError(f"Model checkpoint not found: {checkpoint}")

    images = sorted(dataset_root.glob("sample_*/post/image_*.jpg"))
    if not images:
        raise FileNotFoundError(f"No sample_*/post/image_*.jpg files found in {dataset_root}")
    labels = {
        image.parent.parent.name: read_post_label(image.parent.parent / "meta.txt")
        for image in images
    }
    posts = sorted(labels)

    torch.set_num_threads(4)
    device = torch.device(args.device)
    print(f"Loading models on {device}; {len(posts)} posts, {len(images)} images", flush=True)
    dsam_started = time.perf_counter()
    dsam = DetectiveSAMRunner(checkpoint_path=args.detectivesam_checkpoint, device=args.device)
    dsam_load_ms = (time.perf_counter() - dsam_started) * 1000
    trufor, trufor_load_ms = load_trufor(args.trufor_checkpoint.resolve(), device)

    rows: list[dict] = []
    for index, image_path in enumerate(images, start=1):
        post_id = image_path.parent.parent.name
        print(f"[{index}/{len(images)}] {post_id}/{image_path.name}", flush=True)
        with Image.open(image_path) as original_image:
            input_size = list(original_image.size)

        sample = prepare_sample(
            source_path=image_path,
            target_path=image_path,
            mask_path=None,
            img_size=dsam.config.img_size,
            perturbation_type=dsam.config.perturbation_type,
            perturbation_intensity=dsam.config.perturbation_intensity,
        )
        started = time.perf_counter()
        dsam_prediction = dsam.predict_sample(sample, threshold=args.threshold)
        dsam_latency_ms = (time.perf_counter() - started) * 1000
        dsam_map = np.asarray(dsam_prediction.probability, dtype=np.float32)

        started = time.perf_counter()
        trufor_map, trufor_confidence, trufor_detection_score = trufor_predict(
            trufor, image_path, device
        )
        trufor_latency_ms = (time.perf_counter() - started) * 1000
        trufor_map = np.asarray(trufor_map, dtype=np.float32)
        trufor_confidence = np.asarray(trufor_confidence, dtype=np.float32)

        result_key = f"{post_id}_{image_path.stem}"
        np.savez_compressed(
            map_dir / f"{result_key}.npz",
            detectivesam_localization=dsam_map,
            trufor_localization=trufor_map,
            trufor_confidence=trufor_confidence,
        )
        overlay_panel(
            visualization_dir / f"{result_key}_comparison.png",
            image_path,
            trufor_map,
            dsam_map,
        )

        rows.append(
            {
                "post_id": post_id,
                "post_is_fake": labels[post_id],
                "image_path": str(image_path),
                "image_size_px": input_size,
                "trufor": {
                    "raw_detection_score": trufor_detection_score,
                    "score_semantics": "uncalibrated image-level detector signal, not a probability",
                    "localization_mean": float(trufor_map.mean()),
                    "localization_top_1_percent_mean": top_fraction_mean(trufor_map),
                    "confidence_mean": float(trufor_confidence.mean()),
                    "region_fraction_above_threshold": float((trufor_map > args.threshold).mean()),
                    "latency_ms": trufor_latency_ms,
                },
                "detective_sam": {
                    "localization_mean": float(dsam_map.mean()),
                    "localization_top_1_percent_mean": top_fraction_mean(dsam_map),
                    "region_fraction_above_threshold": float((dsam_map > args.threshold).mean()),
                    "score_semantics": "uncalibrated localization signal, not a probability of falsity",
                    "latency_ms": dsam_latency_ms,
                },
            }
        )

    by_post: list[dict] = []
    for post_id in posts:
        post_rows = [row for row in rows if row["post_id"] == post_id]
        by_post.append(
            {
                "post_id": post_id,
                "is_fake": labels[post_id],
                "image_count": len(post_rows),
                "trufor_mean_detection_score": statistics.mean(
                    row["trufor"]["raw_detection_score"] for row in post_rows
                ),
                "trufor_max_detection_score": max(
                    row["trufor"]["raw_detection_score"] for row in post_rows
                ),
                "detective_sam_mean_top_1_percent_signal": statistics.mean(
                    row["detective_sam"]["localization_top_1_percent_mean"] for row in post_rows
                ),
                "detective_sam_max_top_1_percent_signal": max(
                    row["detective_sam"]["localization_top_1_percent_mean"] for row in post_rows
                ),
            }
        )

    features = {
        "trufor_mean_detection_score": [post["trufor_mean_detection_score"] for post in by_post],
        "trufor_max_detection_score": [post["trufor_max_detection_score"] for post in by_post],
        "detective_sam_mean_top_1_percent_signal": [
            post["detective_sam_mean_top_1_percent_signal"] for post in by_post
        ],
        "detective_sam_max_top_1_percent_signal": [
            post["detective_sam_max_top_1_percent_signal"] for post in by_post
        ],
    }
    post_labels = [labels[post_id] for post_id in posts]
    grouped = {}
    for group_name, fake_value in (("real", False), ("fake", True)):
        group_posts = [post for post in by_post if post["is_fake"] == fake_value]
        grouped[group_name] = {
            "post_count": len(group_posts),
            "metrics": {
                metric: {
                    "mean": float(statistics.mean(post[metric] for post in group_posts)),
                    "median": float(statistics.median(post[metric] for post in group_posts)),
                }
                for metric in features
            },
        }

    payload = {
        "experiment": "TruFor vs Detective SAM on user test package (unlabeled pixel masks)",
        "status": "exploratory_post_label_comparison_no_pixel_ground_truth",
        "dataset_root": str(dataset_root),
        "post_count": len(posts),
        "image_count": len(images),
        "post_labels": {post_id: labels[post_id] for post_id in posts},
        "device": str(device),
        "model_input_note": "Both models analyze the same source images. TruFor receives the original resolution; Detective SAM resizes each image to its configured 512x512 input and receives internally generated perturbation streams. Latency is therefore not a same-pixel-count comparison.",
        "threshold_for_signal_area_only": args.threshold,
        "model_load_ms": {"trufor": trufor_load_ms, "detective_sam": dsam_load_ms},
        "mean_image_latency_ms": {
            model: float(statistics.mean(row[model]["latency_ms"] for row in rows))
            for model in ("trufor", "detective_sam")
        },
        "post_level_group_summary": grouped,
        "exploratory_post_level_auc": {
            metric: score_auc(values, post_labels) for metric, values in features.items()
        },
        "post_level_scores": by_post,
        "image_results": rows,
        "limitations": [
            "The supplied labels describe whether each whole post is fake; they do not label individual images as AI-generated or manipulated.",
            "No pixel-level manipulation masks are present, so localization IoU/F1 and pixel accuracy cannot be calculated.",
            "TruFor detection scores and both localization maps are uncalibrated model signals, not probabilities that an image or post is fake.",
            "The exploratory AUC uses only 10 post-level labels and should not be treated as a validated benchmark.",
            "Detective SAM receives each image plus perturbation streams generated by its configured inference pipeline; TruFor receives the image only.",
        ],
    }
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(json.dumps({
        "post_count": len(posts),
        "image_count": len(images),
        "post_level_group_summary": grouped,
        "exploratory_post_level_auc": payload["exploratory_post_level_auc"],
        "mean_image_latency_ms": payload["mean_image_latency_ms"],
        "output_dir": str(output_dir),
    }, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
