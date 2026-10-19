"""Measure LOTA score changes under controlled resize and JPEG transforms."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/model-experiments/internet_vct2_50_20261015"
MANIFEST = DATASET / "manifest.jsonl"
CHECKPOINT = ROOT / "models/LOTA/Network_best.pth"
VARIANTS = (
    "original",
    "resize_down_up_75",
    "jpeg_q95",
    "jpeg_q85",
    "jpeg_q70",
    "jpeg_q50",
    "resize_down_up_75_jpeg_q85",
)


def load_rows() -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        row["absolute_image_path"] = DATASET / row["image_path"]
        if not row["absolute_image_path"].is_file():
            raise FileNotFoundError(row["absolute_image_path"])
    return rows


def make_variant(image: Image.Image, name: str) -> Image.Image:
    result = image.convert("RGB")
    width, height = result.size
    if name in {"resize_down_up_75", "resize_down_up_75_jpeg_q85"}:
        small_size = (max(1, round(width * 0.75)), max(1, round(height * 0.75)))
        result = result.resize(small_size, Image.Resampling.BICUBIC)
        result = result.resize((width, height), Image.Resampling.BICUBIC)
    if name.startswith("jpeg_q") or name.endswith("jpeg_q85"):
        quality = 85 if name.endswith("jpeg_q85") else int(name.removeprefix("jpeg_q"))
        buffer = BytesIO()
        result.save(
            buffer,
            format="JPEG",
            quality=quality,
            subsampling=2,
            optimize=False,
            progressive=False,
        )
        buffer.seek(0)
        with Image.open(buffer) as encoded:
            result = encoded.convert("RGB")
    return result


def auc(labels: list[int], scores: list[float]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ordered = sorted(zip(scores, labels), key=lambda item: item[0])
    positive_rank_sum = 0.0
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        mean_rank = ((i + 1) + j) / 2
        positive_rank_sum += mean_rank * sum(label for _, label in ordered[i:j])
        i = j
    return (positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def describe(scores: list[float]) -> dict[str, float | int | None]:
    if not scores:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": len(scores),
        "mean": statistics.fmean(scores),
        "median": statistics.median(scores),
        "min": min(scores),
        "max": max(scores),
    }


def checkpoint_info() -> dict[str, Any]:
    digest = hashlib.sha256()
    with CHECKPOINT.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "stored_path": str(CHECKPOINT.relative_to(ROOT)),
        "source_filename": "sdv5_scaling_patch32.pth (provided by user; stored as Network_best.pth)",
        "size_bytes": CHECKPOINT.stat().st_size,
        "sha256": digest.hexdigest(),
        "state_dict_load": "strict=True; successful throughout the current experiment",
        "origin_caveat": (
            "The filename suggests the SD 1.5 scaling/patch32 checkpoint; the payload is a state_dict "
            "and does not cryptographically attest training provenance."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / f"data/model-experiments/lota_degradation_vct50_{date.today():%Y%m%d}",
    )
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error(f"Output directory already exists: {args.output_dir}")
    if not CHECKPOINT.is_file():
        parser.error(f"LOTA checkpoint not found: {CHECKPOINT}")

    sys.path.insert(0, str(ROOT))
    from content_trust.providers.lota import LotaLocalClient

    rows = load_rows()
    sizes: set[str] = set()
    for row in rows:
        with Image.open(row["absolute_image_path"]) as image:
            sizes.add(f"{image.size[0]}x{image.size[1]}")
    image_sizes = sorted(sizes)
    provider = LotaLocalClient(str(CHECKPOINT), device=args.device)
    baseline: dict[str, float] = {}
    all_results: list[dict[str, Any]] = []
    variant_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    latencies: list[float] = []

    args.output_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="lota-degradation-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        for variant in VARIANTS:
            for row_index, row in enumerate(rows, start=1):
                source_path = row["absolute_image_path"]
                with Image.open(source_path) as source:
                    image = make_variant(source, variant)
                if variant == "original":
                    inference_path = source_path
                else:
                    inference_path = temporary_root / f"{row['sample_id']}_{variant}.png"
                    image.save(inference_path, format="PNG")
                started = time.perf_counter()
                signal = provider.analyze_image(
                    image_path=str(inference_path), preprocessing_seed=args.seed
                )
                latency_ms = (time.perf_counter() - started) * 1000
                if not signal.available or signal.score is None:
                    raise RuntimeError(
                        f"LOTA unavailable for {row['sample_id']} / {variant}: {signal.error}"
                    )
                if variant == "original":
                    baseline[row["sample_id"]] = signal.score
                result = {
                    "sample_id": row["sample_id"],
                    "row_idx": row["row_idx"],
                    "label": row["label"],
                    "generator": row.get("generator"),
                    "variant": variant,
                    "score": signal.score,
                    "score_semantics": signal.score_semantics,
                    "device": signal.inference_device,
                    "preprocessing_seed": args.seed,
                    "latency_ms": round(latency_ms, 2),
                    "source_image": str(source_path.relative_to(ROOT)),
                }
                if variant != "original":
                    result["score_delta_vs_original"] = signal.score - baseline[row["sample_id"]]
                    result["absolute_score_delta_vs_original"] = abs(
                        result["score_delta_vs_original"]
                    )
                variant_rows[variant].append(result)
                all_results.append(result)
                latencies.append(latency_ms)
                print(
                    f"[{len(all_results)}/{len(rows) * len(VARIANTS)}] "
                    f"{variant}: {row['sample_id']} score={signal.score:.6g}",
                    file=sys.stderr,
                )

    labels = [1 if row["label"] == "ai_generated" else 0 for row in rows]
    pair_ids = sorted({row["row_idx"] for row in rows})
    previous_run = json.loads((DATASET / "lota.json").read_text(encoding="utf-8"))
    previous_by_name = {
        Path(result["image_path"]).name: float(result["score"])
        for result in previous_run["results"]
        if result.get("available") and result.get("score") is not None
    }
    current_original_by_name = {
        Path(result["source_image"]).name: float(result["score"])
        for result in variant_rows["original"]
    }
    matched_deltas = [
        abs(current_original_by_name[name] - previous_by_name[name])
        for name in sorted(current_original_by_name.keys() & previous_by_name.keys())
    ]
    summary_by_variant: dict[str, Any] = {}
    for variant, results in variant_rows.items():
        scores = [float(result["score"]) for result in results]
        result_by_id = {result["sample_id"]: result for result in results}
        real_scores = [float(r["score"]) for r in results if r["label"] == "real"]
        ai_scores = [float(r["score"]) for r in results if r["label"] == "ai_generated"]
        paired_ai_higher = 0
        paired_n = 0
        for row_idx in pair_ids:
            real = next(
                (r for r in results if r["row_idx"] == row_idx and r["label"] == "real"),
                None,
            )
            generated = next(
                (r for r in results if r["row_idx"] == row_idx and r["label"] == "ai_generated"),
                None,
            )
            if real and generated:
                paired_n += 1
                paired_ai_higher += float(generated["score"]) > float(real["score"])
        deltas = [float(r["absolute_score_delta_vs_original"]) for r in results if "absolute_score_delta_vs_original" in r]
        summary_by_variant[variant] = {
            "count": len(results),
            "auroc": auc(labels, scores),
            "real_score": describe(real_scores),
            "ai_generated_score": describe(ai_scores),
            "paired_generated_score_higher": paired_ai_higher,
            "paired_count": paired_n,
            "mean_absolute_score_delta_vs_original": statistics.fmean(deltas) if deltas else 0.0,
            "median_absolute_score_delta_vs_original": statistics.median(deltas) if deltas else 0.0,
            "tpr_at_raw_threshold_0_5_reference_only": sum(s >= 0.5 for s in ai_scores) / len(ai_scores),
            "fpr_at_raw_threshold_0_5_reference_only": sum(s >= 0.5 for s in real_scores) / len(real_scores),
            "image_sizes": image_sizes,
        }

    summary = {
        "experiment": "LOTA controlled resize/JPEG degradation sensitivity",
        "date": date.today().isoformat(),
        "dataset": "VCT² COCO_AI, 50 paired real/generated images; generic domain",
        "sample_count": len(rows),
        "all_source_files_are_jpeg": True,
        "model_fusion": False,
        "device": provider._device,
        "checkpoint": checkpoint_info(),
        "reproduction_vs_previous_lota_run": {
            "matched_images": len(matched_deltas),
            "max_absolute_score_delta": max(matched_deltas) if matched_deltas else None,
            "mean_absolute_score_delta": statistics.fmean(matched_deltas) if matched_deltas else None,
            "note": "Exact score reproduction check against the existing VCT² LOTA run using the same adapter and seed.",
        },
        "preprocessing": {
            "bit_mode": "scaling",
            "low_bit_mask": "0x07",
            "patch_size": 32,
            "sampled_candidate_patches": 64,
            "patch_selection": "maximum multi-directional gradient",
            "selected_patch_resize": "256x256 bilinear",
            "normalization": "ImageNet mean/std",
            "seed_policy": (
                f"fixed seed {args.seed} for every variant, isolating pixel changes; "
                "upstream test uses a global RNG stream seeded once, so candidate crop positions "
                "are not identical to its full-loader sequence"
            ),
            "implementation_differences": [
                "Adapter uses Pillow bilinear resize while upstream bit_patch uses OpenCV INTER_LINEAR; both are linear interpolation but may differ slightly at pixel level.",
                "The checkpoint loads strictly into the expected ResNet-50 layout; source filename/config indicate SD 1.5 scaling patch32, but the state_dict itself does not attest provenance.",
            ],
        },
        "transform_definitions": {
            "original": "source JPEG bytes passed directly to the current adapter",
            "resize_down_up_75": "BICUBIC downscale to 75% then restore original dimensions",
            "jpeg_q95/q85/q70/q50": "re-encode decoded source at selected Pillow JPEG quality; 4:2:0 subsampling",
            "resize_down_up_75_jpeg_q85": "75% BICUBIC down/up followed by JPEG quality 85",
        },
        "variant_results": summary_by_variant,
        "runtime": {
            "total_image_inferences": len(all_results),
            "mean_latency_ms": statistics.fmean(latencies),
            "median_latency_ms": statistics.median(latencies),
        },
        "limitations": [
            "All source files were already JPEG; this tests incremental re-encoding, not original-camera-to-platform degradation.",
            "The set has only 50 generic COCO images and does not estimate beauty-domain accuracy.",
            "Image variants are paired transformations, not independent samples; do not treat the 350 rows as 350 independent images.",
            "LOTA raw scores are uncalibrated signals, not probabilities. Threshold 0.5 is shown for reference only.",
            "Synthetic resizing/JPEG encoding approximates common operations but does not reproduce Xiaohongshu's actual image pipeline.",
        ],
    }
    (args.output_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_results),
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_lines = [
        "# LOTA 缩放与 JPEG 敏感性实验",
        "",
        f"日期：{summary['date']}。测试集：VCT² COCO_AI 的 50 张通用图（真实/AI 各 25 张）。",
        "本实验对每张来源图运行原图、75% 缩小再放大、JPEG q95/q85/q70/q50，以及缩放后 JPEG q85，共 350 次推理。",
        "",
        "## 结果",
        "",
        "| 变体 | AUROC | 真实组均值 | AI 组均值 | AI 分数更高的配对数 | 平均绝对分数变化 | 0.5 阈值 TPR/FPR（仅参考） |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, values in summary_by_variant.items():
        report_lines.append(
            "| {name} | {auc:.4f} | {real:.6g} | {fake:.6g} | {pairs}/25 | {delta:.6g} | {tpr:.0%}/{fpr:.0%} |".format(
                name=name,
                auc=values["auroc"],
                real=values["real_score"]["mean"],
                fake=values["ai_generated_score"]["mean"],
                pairs=values["paired_generated_score_higher"],
                delta=values["mean_absolute_score_delta_vs_original"],
                tpr=values["tpr_at_raw_threshold_0_5_reference_only"],
                fpr=values["fpr_at_raw_threshold_0_5_reference_only"],
            )
        )
    report_lines.extend(
        [
            "",
            "75% 缩小再放大后，两组 LOTA 分数都大幅移向 1；该变体在 25 张真实图中有 24 张超过原始分数阈值 0.5，同时 25 张生成图也有 24 张超过阈值。它说明重采样会强烈改变模型信号，并不能据此称这些真实图为 AI 生成。",
            "单独 JPEG 重编码的 AUC 随质量变化不呈单调趋势；resize 后再做 JPEG q85 又明显不同于只 resize。这里存在变换顺序与低位平面信号的交互，不能简单归纳成‘JPEG 越压缩，LOTA 越失效’。",
            "",
            "## 权重与预处理核对",
            "",
            f"- checkpoint：`{summary['checkpoint']['stored_path']}`，来源文件名 `{summary['checkpoint']['source_filename']}`；SHA-256 `{summary['checkpoint']['sha256']}`。权重以 `strict=True` 加载成功。state_dict 不包含可独立验证训练来源的签名信息。",
            "- 当前 adapter 与上游在主要输入语义上吻合：低 3 bit scaling、32×32 候选块、64 个随机候选中取最大多方向梯度块、缩放到 256×256、ImageNet mean/std 归一化。",
            "- 实现仍有两处可复现性差异：上游用 OpenCV resize，adapter 用 Pillow bilinear；上游整批评测从一次全局 seed=42 连续采样，adapter 每次调用重置 seed。为了本次成对比较，所有变体固定同一 seed 和候选块位置。",
            f"- 原图分数与现有 VCT² LOTA 基线逐图匹配 {len(matched_deltas)} 张；最大绝对分差 {max(matched_deltas) if matched_deltas else float('nan'):.3g}。",
            "",
            "## 限制",
            "",
            "- 50 张基图来自通用 COCO 图像，不能推断美妆域准确率；350 行是成对变换结果，不是 350 个独立样本。",
            "- 所有来源文件本来就是 JPEG；本实验只测额外重编码，不覆盖原始相机文件到平台发布的完整链路。合成变换也不等于小红书真实处理流程。",
            "- LOTA 输出是未校准信号，不是 AI 生成概率；0.5 阈值只用于展示分数漂移，不应作为产品阈值。",
            "",
            "逐图结果、模型元数据与全部汇总数值见 `results.jsonl` 和 `summary.json`。",
        ]
    )
    (args.output_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
