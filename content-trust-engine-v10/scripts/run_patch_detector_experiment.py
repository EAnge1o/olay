"""Compare whole-image and tiled inference for LOTA and FerretNet.

This is an experimental runner. It does not change production scoring.
Both detectors receive 256x256 image tiles; LOTA then applies its existing
high-gradient 32x32 bit-plane patch selection inside each tile.
"""

from __future__ import annotations

import json
import math
import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPERIMENT_DIR = ROOT / "data/model-experiments/internet_vct2_50_20261015"
OUTPUT_DIR = EXPERIMENT_DIR / "patch_inference_20261015"
TILE_SIZE = 256


def _positions(length: int, tile_size: int, stride_pixels: int | None) -> list[int]:
    if length <= tile_size:
        return [0]
    # Keep a single central crop for near-tile-sized images. Otherwise distribute
    # tiles evenly from the first to last edge so all source pixels are covered.
    if length <= int(tile_size * 1.25):
        return [(length - tile_size) // 2]
    if stride_pixels is not None:
        last = length - tile_size
        positions = list(range(0, last + 1, stride_pixels))
        if positions[-1] != last:
            positions.append(last)
        return positions
    count = math.ceil(length / tile_size)
    return [round(i * (length - tile_size) / (count - 1)) for i in range(count)]


def _make_tiles(
    image: Any, stride_pixels: int | None
) -> tuple[list[tuple[Any, tuple[int, int, int, int]]], tuple[int, int]]:
    from PIL import ImageOps

    source_width, source_height = image.size
    pad_left = max(0, (TILE_SIZE - source_width) // 2)
    pad_top = max(0, (TILE_SIZE - source_height) // 2)
    pad_right = max(0, TILE_SIZE - source_width - pad_left)
    pad_bottom = max(0, TILE_SIZE - source_height - pad_top)
    padded = ImageOps.expand(
        image,
        border=(pad_left, pad_top, pad_right, pad_bottom),
        fill=(0, 0, 0),
    )
    width, height = padded.size
    tiles = []
    for top in _positions(height, TILE_SIZE, stride_pixels):
        for left in _positions(width, TILE_SIZE, stride_pixels):
            tile = padded.crop((left, top, left + TILE_SIZE, top + TILE_SIZE))
            # Coordinates point back to the unpadded source image and are clipped
            # where a narrow image needed padding.
            bbox = (
                max(0, left - pad_left),
                max(0, top - pad_top),
                min(source_width, left + TILE_SIZE - pad_left),
                min(source_height, top + TILE_SIZE - pad_top),
            )
            tiles.append((tile, bbox))
    return tiles, (source_width, source_height)


def _auc(labels: list[int], scores: list[float]) -> float | None:
    positives = [score for label, score in zip(labels, scores) if label == 1]
    negatives = [score for label, score in zip(labels, scores) if label == 0]
    if not positives or not negatives:
        return None
    wins = sum(
        1.0 if positive > negative else 0.5 if positive == negative else 0.0
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _paired_wins(records: list[dict[str, Any]], score_key: str) -> tuple[int, int]:
    by_row: dict[int, dict[str, dict[str, Any]]] = {}
    for record in records:
        by_row.setdefault(record["row_idx"], {})[record["label"]] = record
    pairs = [pair for pair in by_row.values() if "real" in pair and "ai_generated" in pair]
    wins = sum(
        pair["ai_generated"][score_key] > pair["real"][score_key]
        for pair in pairs
    )
    return wins, len(pairs)


def _summarize(records: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    labels = [1 if item["label"] == "ai_generated" else 0 for item in records]
    scores = [float(item[score_key]) for item in records]
    real = [score for label, score in zip(labels, scores) if label == 0]
    generated = [score for label, score in zip(labels, scores) if label == 1]
    paired_wins, paired_count = _paired_wins(records, score_key)
    return {
        "roc_auc": _auc(labels, scores),
        "real_mean": sum(real) / len(real) if real else None,
        "generated_mean": sum(generated) / len(generated) if generated else None,
        "generated_score_higher_in_paired_rows": paired_wins,
        "paired_rows": paired_count,
        "above_0_5_real": sum(score >= 0.5 for score in real),
        "above_0_5_generated": sum(score >= 0.5 for score in generated),
    }


def _run_model(
    model_name: str,
    manifest: list[dict[str, Any]],
    settings: Any,
    output_dir: Path,
    stride_pixels: int | None,
) -> list[dict[str, Any]]:
    from PIL import Image, ImageDraw

    from content_trust.providers.ferretnet import FerretNetLocalClient
    from content_trust.providers.lota import LotaLocalClient

    if model_name == "lota":
        provider = LotaLocalClient(
            settings.lota_weights_path,
            device=settings.lota_device,
            timeout_seconds=settings.timeout_seconds,
        )
    else:
        provider = FerretNetLocalClient(
            settings.ferretnet_weights_path,
            repo_path=settings.ferretnet_repo_path,
            device=settings.ferretnet_device,
            timeout_seconds=settings.timeout_seconds,
        )

    model_output = output_dir / model_name
    best_patch_dir = model_output / "best_patches"
    best_patch_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    failures = 0
    for image_index, item in enumerate(manifest, start=1):
        image_path = EXPERIMENT_DIR / item["image_path"]
        print(
            f"[{model_name} {image_index}/{len(manifest)}] {item['filename']}",
            file=sys.stderr,
            flush=True,
        )
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        tiles, (source_width, source_height) = _make_tiles(image, stride_pixels)
        tile_records: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="trust-patch-") as temp_dir:
            for tile_index, (tile, bbox) in enumerate(tiles):
                tile_path = Path(temp_dir) / f"tile_{tile_index:03d}.png"
                tile.save(tile_path, format="PNG")
                started = time.perf_counter()
                signal = provider.analyze_image(image_path=str(tile_path))
                latency_ms = (time.perf_counter() - started) * 1000.0
                x0, y0, x1, y1 = bbox
                tile_records.append(
                    {
                        "tile_index": tile_index,
                        "bbox_xyxy_pixels": [x0, y0, x1, y1],
                        "bbox_xyxy_normalized": [
                            round(x0 / source_width, 6),
                            round(y0 / source_height, 6),
                            round(x1 / source_width, 6),
                            round(y1 / source_height, 6),
                        ],
                        "score": signal.score,
                        "available": signal.available,
                        "error": signal.error,
                        "latency_ms": round(latency_ms, 2),
                    }
                )
                if not signal.available:
                    failures += 1

                # Keep the source crop for the top-scoring tile only after ranking.
                tile_records[-1]["_tile_file"] = str(tile_path)

            available_tiles = [tile for tile in tile_records if tile["available"]]
            if available_tiles:
                best_tile = max(available_tiles, key=lambda tile: float(tile["score"]))
                scores = [float(tile["score"]) for tile in available_tiles]
                top_three = sorted(scores, reverse=True)[:3]
                best_patch_path = best_patch_dir / f"{item['sample_id']}.png"
                with Image.open(best_tile["_tile_file"]) as best_patch:
                    best_patch.convert("RGB").save(best_patch_path)
                # Save a compact original-image overlay so the selected area is
                # visible without changing the source image.
                overlay = image.copy()
                draw = ImageDraw.Draw(overlay)
                x0, y0, x1, y1 = best_tile["bbox_xyxy_pixels"]
                for width in range(5, 0, -1):
                    draw.rectangle((x0, y0, x1, y1), outline=(255, 0, 0), width=width)
                overlay_path = best_patch_dir / f"{item['sample_id']}_overlay.jpg"
                overlay.save(overlay_path, quality=92)
                best_tile = {key: value for key, value in best_tile.items() if not key.startswith("_")}
                record = {
                    "sample_id": item["sample_id"],
                    "row_idx": item["row_idx"],
                    "label": item["label"],
                    "generator": item["generator"],
                    "caption": item["caption"],
                    "image_path": item["image_path"],
                    "tile_count": len(tiles),
                    "available_tile_count": len(available_tiles),
                    "mean_tile_score": sum(scores) / len(scores),
                    "top3_tile_score": sum(top_three) / len(top_three),
                    "max_tile_score": max(scores),
                    "best_tile": best_tile,
                    "best_patch_path": str(best_patch_path.relative_to(output_dir)),
                    "overlay_path": str(overlay_path.relative_to(output_dir)),
                    "tile_latency_ms": round(
                        sum(tile["latency_ms"] for tile in tile_records), 2
                    ),
                    "tiles": [
                        {key: value for key, value in tile.items() if not key.startswith("_")}
                        for tile in tile_records
                    ],
                }
            else:
                record = {
                    "sample_id": item["sample_id"],
                    "row_idx": item["row_idx"],
                    "label": item["label"],
                    "generator": item["generator"],
                    "caption": item["caption"],
                    "image_path": item["image_path"],
                    "tile_count": len(tiles),
                    "available_tile_count": 0,
                    "mean_tile_score": None,
                    "top3_tile_score": None,
                    "max_tile_score": None,
                    "best_tile": None,
                    "tile_latency_ms": round(sum(tile["latency_ms"] for tile in tile_records), 2),
                    "tiles": [
                        {key: value for key, value in tile.items() if not key.startswith("_")}
                        for tile in tile_records
                    ],
                }
        records.append(record)

    scored = [record for record in records if record["max_tile_score"] is not None]
    baseline_path = EXPERIMENT_DIR / f"{model_name}.json"
    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_by_path = {
        Path(item["image_path"]).name: item for item in baseline_data["results"]
    }
    for record in records:
        baseline = baseline_by_path.get(Path(record["image_path"]).name)
        record["whole_image_score"] = baseline.get("score") if baseline else None

    payload = {
        "experiment": f"{model_name} 256x256 tiled inference",
        "status": "experimental_unvalidated_on_beauty_images",
        "score_note": "Uncalibrated model signal; not a probability.",
        "model_fusion": False,
        "tile_size": TILE_SIZE,
        "tile_stride_pixels": stride_pixels,
        "tile_policy": (
            f"edge-anchored grid with {stride_pixels}px stride; one centered tile when dimension <= 1.25x tile size"
            if stride_pixels is not None
            else "coverage grid with evenly distributed overlap; one centered tile when dimension <= 1.25x tile size"
        ),
        "score_aggregation": {
            "mean_tile_score": "average over available tile scores",
            "top3_tile_score": "average of the three highest available tile scores, or all scores if fewer than three",
            "max_tile_score": "maximum available tile score; candidate localized signal, subject to max-pooling bias",
        },
        "failures": failures,
        "image_count": len(records),
        "metrics": {
            "whole_image": _summarize(
                [record for record in records if record["whole_image_score"] is not None],
                "whole_image_score",
            ),
            "mean_tile": _summarize(scored, "mean_tile_score"),
            "top3_tile": _summarize(scored, "top3_tile_score"),
            "max_tile": _summarize(scored, "max_tile_score"),
            "average_tile_count": sum(record["tile_count"] for record in records) / max(1, len(records)),
            "average_tile_latency_ms_per_image": sum(record["tile_latency_ms"] for record in records) / max(1, len(records)),
        },
        "results": records,
    }
    (model_output / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return records


def _report(model_results: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# 图像块推理实验",
        "",
        "- 数据：与整图基线相同的 50 张 VCT² COCO_AI 图像（25 real、25 generated），caption 保留在逐图结果中。",
        "- 方法：按 256×256 滑窗块逐块推理；较大图片均匀铺块，尺寸接近单块时取中心块。比较整图、分块均值、Top-3 块均值和最高块分。",
        "- LOTA：每个输入块内部仍执行其原有的低位平面高梯度 32×32 patch 选择。",
        "- 分数未校准，不是生成概率；最高块聚合会受到块数和 max-pooling 的影响。实验只用于模型比较，不改变生产流程。",
        "",
        "## 指标对照",
        "",
        "| 模型 | 聚合 | AUC | Real 均分 | Generated 均分 | 配对 generated 更高 | 平均块数/图 | 平均耗时/图 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model_name, payload in model_results.items():
        metrics = payload["metrics"]
        for aggregation, label in (("whole_image", "整图基线"), ("mean_tile", "分块均值"), ("top3_tile", "Top-3 块均值"), ("max_tile", "最高分块")):
            values = metrics[aggregation]
            auc = values["roc_auc"]
            auc_text = "n/a" if auc is None else f"{auc:.4f}"
            real_mean = values["real_mean"]
            generated_mean = values["generated_mean"]
            paired = f"{values['generated_score_higher_in_paired_rows']}/{values['paired_rows']}"
            tile_count = "—" if aggregation == "whole_image" else f"{metrics['average_tile_count']:.1f}"
            latency = "—" if aggregation == "whole_image" else f"{metrics['average_tile_latency_ms_per_image']:.1f} ms"
            lines.append(
                f"| {model_name} | {label} | {auc_text} | {real_mean:.3g} | {generated_mean:.3g} | {paired} | {tile_count} | {latency} |"
            )
    lines.extend(
        [
            "",
            "## 逐图查看",
            "",
            "每个模型目录下 `best_patches/` 保存最高分块裁剪图及原图红框标注。`results.json` 保留原文 caption、全部块分数、坐标、可用状态和耗时。",
            "",
            "## 解读边界",
            "",
            "分块 AUC 若高于整图基线，只说明这批样本上的排序有所改善；图像分辨率会影响块数，最高分块也可能随块数增加而抬高，不能直接用原整图阈值。需要结合真实社媒压缩、美妆图和独立标注样本重新校准。局部红框表示模型分数最高的输入块，不等于篡改区域的证明。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tiled local image-detector experiments.")
    parser.add_argument(
        "--stride-pixels",
        type=int,
        default=None,
        help="fixed tile stride; omit to use the original adaptive coverage grid",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="directory for experimental JSON and patch overlays",
    )
    args = parser.parse_args()
    if args.stride_pixels is not None and args.stride_pixels < 1:
        parser.error("--stride-pixels must be a positive integer")

    from content_trust.config import Settings

    manifest = [
        json.loads(line)
        for line in (EXPERIMENT_DIR / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    all_results: dict[str, dict[str, Any]] = {}
    for model_name in ("lota", "ferretnet"):
        records = _run_model(
            model_name,
            manifest,
            settings,
            output_dir,
            args.stride_pixels,
        )
        payload = json.loads((output_dir / model_name / "results.json").read_text(encoding="utf-8"))
        all_results[model_name] = payload
        print(
            f"{model_name}: whole={payload['metrics']['whole_image']['roc_auc']:.4f}, "
            f"mean_tile={payload['metrics']['mean_tile']['roc_auc']:.4f}, "
            f"top3_tile={payload['metrics']['top3_tile']['roc_auc']:.4f}, "
            f"max_tile={payload['metrics']['max_tile']['roc_auc']:.4f}; "
            f"unavailable tile calls={payload['failures']}",
            file=sys.stderr,
            flush=True,
        )
        if any(record["max_tile_score"] is None for record in records):
            print(f"Warning: {model_name} had images with no available tile score.", file=sys.stderr)
    (output_dir / "report.md").write_text(_report(all_results), encoding="utf-8")
    print(f"Saved experiment artifacts to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
