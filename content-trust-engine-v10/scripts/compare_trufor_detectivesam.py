"""Compare TruFor and Detective SAM on the same paired forgery samples.

This is an offline model experiment; it does not modify the production pipeline.
Run with the isolated evaluation environment used for both upstream models.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/ct-trufor-mpl")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DETECTIVESAM_ROOT = ROOT / "models" / "DetectiveSAM"
TRUFOR_TRAIN_ROOT = ROOT / "models" / "TruFor" / "TruFor_train_test"

sys.path.insert(0, str(DETECTIVESAM_ROOT))
sys.path.insert(0, str(TRUFOR_TRAIN_ROOT))

from detectivesam_inference.dataset import PairDataset  # noqa: E402
from detectivesam_inference.metrics import compute_f1, compute_iou  # noqa: E402
from detectivesam_inference.runtime import DetectiveSAMRunner  # noqa: E402
from lib.config import config as trufor_config  # noqa: E402
from lib.config import update_config as update_trufor_config  # noqa: E402
from lib.utils import get_model as get_trufor_model  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=ROOT / "models" / "DetectiveSAM" / "demo" / "cocoglide",
    )
    parser.add_argument(
        "--trufor-checkpoint",
        type=Path,
        default=TRUFOR_TRAIN_ROOT / "pretrained_models" / "trufor.pth.tar",
    )
    parser.add_argument(
        "--detectivesam-checkpoint",
        type=Path,
        default=DETECTIVESAM_ROOT
        / "checkpoints"
        / "model_epoch22_batch999_score1.1114.pth",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT
        / "data"
        / "model-experiments"
        / "trufor_detectivesam_cocoglide_20261015",
    )
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def binary_metrics(probability: np.ndarray, truth: np.ndarray, threshold: float) -> dict[str, float]:
    predicted = probability > threshold
    truth = truth.astype(bool)
    tp = int(np.logical_and(predicted, truth).sum())
    fp = int(np.logical_and(predicted, ~truth).sum())
    fn = int(np.logical_and(~predicted, truth).sum())
    tn = int(np.logical_and(~predicted, ~truth).sum())
    return {
        "iou": compute_iou(predicted.astype(np.uint8), truth.astype(np.uint8)),
        "f1": compute_f1(predicted.astype(np.uint8), truth.astype(np.uint8)),
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "predicted_region_fraction": float(predicted.mean()),
        "true_region_fraction": float(truth.mean()),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def load_trufor(checkpoint_path: Path, device: torch.device) -> tuple[torch.nn.Module, float]:
    previous_cwd = Path.cwd()
    os.chdir(TRUFOR_TRAIN_ROOT)
    started = time.perf_counter()
    args = SimpleNamespace(
        experiment="trufor_ph3",
        gpu=-1 if device.type == "cpu" else 0,
        opts=["TEST.MODEL_FILE", str(checkpoint_path.resolve())],
    )
    update_trufor_config(trufor_config, args)
    model = get_trufor_model(trufor_config)
    # This is the official checkpoint downloaded from the authors' project page.
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device).eval()
    os.chdir(previous_cwd)
    return model, (time.perf_counter() - started) * 1000


def trufor_predict(
    model: torch.nn.Module, image_path: Path, device: torch.device
) -> tuple[np.ndarray, np.ndarray, float]:
    image = np.asarray(Image.open(image_path).convert("RGB"))
    tensor = torch.from_numpy(image.transpose(2, 0, 1).copy()).float().unsqueeze(0).to(device) / 256.0
    with torch.inference_mode():
        prediction, confidence, detection, _ = model(tensor, save_np=False)
        localization = F.softmax(prediction.squeeze(0), dim=0)[1].cpu().numpy()
        confidence_map = torch.sigmoid(confidence.squeeze(0))[0].cpu().numpy()
        detection_score = float(torch.sigmoid(detection).item())
    return localization, confidence_map, detection_score


def save_comparison(
    path: Path,
    sample,
    truth: np.ndarray,
    dsam_probability: np.ndarray,
    trufor_map: np.ndarray,
    trufor_confidence: np.ndarray,
    dsam_stats: dict[str, float],
    trufor_stats: dict[str, float],
    trufor_score: float,
) -> None:
    panels = [
        ("Source (context only)", np.asarray(sample.source_image)),
        ("Target", np.asarray(sample.target_image)),
        ("Ground truth mask", truth, "gray"),
        (f"Detective SAM prob. (IoU {dsam_stats['iou']:.3f})", dsam_probability, "magma"),
        (f"TruFor localization (IoU {trufor_stats['iou']:.3f})", trufor_map, "magma"),
        ("TruFor confidence map", trufor_confidence, "gray"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(13, 8), constrained_layout=True)
    for axis, panel in zip(axes.flat, panels):
        title, values, *cmap = panel
        axis.imshow(values, cmap=cmap[0] if cmap else None, vmin=0 if cmap else None, vmax=1 if cmap else None)
        axis.set_title(title)
        axis.axis("off")
    fig.suptitle(f"{sample.name} | TruFor raw detector score: {trufor_score:.3f}")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    dataset_root = args.dataset_root.resolve()
    output_dir = args.output_dir.resolve()
    vis_dir = output_dir / "visualizations"
    maps_dir = output_dir / "maps"
    vis_dir.mkdir(parents=True, exist_ok=True)
    maps_dir.mkdir(parents=True, exist_ok=True)

    if not args.trufor_checkpoint.is_file():
        raise FileNotFoundError(f"TruFor checkpoint not found: {args.trufor_checkpoint}")
    if not args.detectivesam_checkpoint.is_file():
        raise FileNotFoundError(f"Detective SAM checkpoint not found: {args.detectivesam_checkpoint}")

    torch.set_num_threads(4)
    device = torch.device(args.device)
    dsam_started = time.perf_counter()
    dsam = DetectiveSAMRunner(checkpoint_path=args.detectivesam_checkpoint, device=args.device)
    dsam_load_ms = (time.perf_counter() - dsam_started) * 1000
    dataset = PairDataset(
        root_dir=dataset_root,
        img_size=dsam.config.img_size,
        perturbation_type=dsam.config.perturbation_type,
        perturbation_intensity=dsam.config.perturbation_intensity,
    )

    trufor, trufor_load_ms = load_trufor(args.trufor_checkpoint.resolve(), device)
    rows: list[dict] = []
    for sample in dataset:
        truth = sample.mask.squeeze().numpy().astype(bool)

        started = time.perf_counter()
        dsam_prediction = dsam.predict_sample(sample, threshold=args.threshold)
        dsam_latency_ms = (time.perf_counter() - started) * 1000
        dsam_probability = dsam_prediction.probability
        dsam_stats = binary_metrics(dsam_probability, truth, args.threshold)

        started = time.perf_counter()
        trufor_map, trufor_confidence, trufor_score = trufor_predict(
            trufor, sample.target_path, device
        )
        trufor_latency_ms = (time.perf_counter() - started) * 1000
        if trufor_map.shape != truth.shape:
            trufor_map = np.asarray(
                Image.fromarray(trufor_map.astype(np.float32), mode="F").resize(
                    (truth.shape[1], truth.shape[0]), Image.Resampling.BILINEAR
                )
            )
            trufor_confidence = np.asarray(
                Image.fromarray(trufor_confidence.astype(np.float32), mode="F").resize(
                    (truth.shape[1], truth.shape[0]), Image.Resampling.BILINEAR
                )
            )
        trufor_stats = binary_metrics(trufor_map, truth, args.threshold)

        np.savez_compressed(
            maps_dir / f"{sample.name}.npz",
            truth=truth.astype(np.uint8),
            detectivesam_probability=dsam_probability.astype(np.float32),
            trufor_localization=trufor_map.astype(np.float32),
            trufor_confidence=trufor_confidence.astype(np.float32),
        )
        Image.fromarray((dsam_probability.clip(0, 1) * 255).astype(np.uint8)).save(
            vis_dir / f"{sample.name}_detectivesam_probability.png"
        )
        Image.fromarray((trufor_map.clip(0, 1) * 255).astype(np.uint8)).save(
            vis_dir / f"{sample.name}_trufor_localization.png"
        )
        Image.fromarray((trufor_confidence.clip(0, 1) * 255).astype(np.uint8)).save(
            vis_dir / f"{sample.name}_trufor_confidence.png"
        )
        Image.fromarray(truth.astype(np.uint8) * 255).save(vis_dir / f"{sample.name}_ground_truth.png")
        save_comparison(
            vis_dir / f"{sample.name}_comparison.png",
            sample,
            truth,
            dsam_probability,
            trufor_map,
            trufor_confidence,
            dsam_stats,
            trufor_stats,
            trufor_score,
        )
        rows.append(
            {
                "sample_id": sample.name,
                "target_image": str(sample.target_path),
                "mask_image": str(sample.mask_path) if sample.mask_path else None,
                "trufor_raw_detection_score": trufor_score,
                "trufor_score_semantics": "sigmoid of the trained image-level detector output; uncalibrated, not a probability",
                "trufor": {**trufor_stats, "latency_ms": trufor_latency_ms},
                "detective_sam": {**dsam_stats, "latency_ms": dsam_latency_ms},
            }
        )
        print(
            f"{sample.name}: TruFor IoU={trufor_stats['iou']:.3f}, "
            f"F1={trufor_stats['f1']:.3f}; Detective SAM IoU={dsam_stats['iou']:.3f}, "
            f"F1={dsam_stats['f1']:.3f}"
        )

    summary: dict[str, dict[str, float]] = {}
    for model_key in ("trufor", "detective_sam"):
        summary[model_key] = {
            metric: float(np.mean([row[model_key][metric] for row in rows]))
            for metric in ("iou", "f1", "precision", "recall", "predicted_region_fraction")
        }
        summary[model_key]["mean_latency_ms"] = float(
            np.mean([row[model_key]["latency_ms"] for row in rows])
        )

    payload = {
        "experiment": "TruFor vs Detective SAM localization comparison",
        "status": "experimental_two_sample_official_demo_only",
        "dataset": "Detective SAM official CoCoGlide paired demo",
        "dataset_root": str(dataset_root),
        "sample_count": len(rows),
        "device": str(device),
        "threshold": args.threshold,
        "metric_note": "Per-image binary mask metrics at the same fixed threshold; macro average over two samples.",
        "input_note": "Detective SAM receives the target and internally generated perturbation streams; TruFor receives the target image only. The source image is shown for context, not passed into either model.",
        "score_note": "TruFor detector score and both localization maps are uncalibrated model outputs, not probabilities of falsity.",
        "model_load_ms": {"trufor": trufor_load_ms, "detective_sam": dsam_load_ms},
        "summary": summary,
        "samples": rows,
    }
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"summary": summary, "output_dir": str(output_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
