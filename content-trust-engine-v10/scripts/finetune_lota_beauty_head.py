"""Run an isolated, head-only LOTA fine-tuning pilot on the XHS sample set.

Only source-disclosed AI images are used as positive training examples. Images
with uncertain visual labels are excluded. The six positive images come from a
single post, so this script is exploratory and does not produce a validated
beauty-domain detector.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "data/datasets/xhs_beauty_seed_50_20261016"
OUTPUT_DIR = ROOT / "data/model-experiments/lota_beauty_finetune_20261016"
BASE_CHECKPOINT = ROOT / "models/LOTA/Network_best.pth"
EXPERIMENT_CHECKPOINT = OUTPUT_DIR / "lota_beauty_head_experimental.pth"

sys.path.insert(0, str(ROOT))

from content_trust.providers.lota import _build_lota_model, _preprocess  # noqa: E402


SEED = 20261016
EPOCHS = 60
LEARNING_RATE = 1e-3
ANCHOR_STRENGTH = 0.02


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _features(model: Any, tensor: Any) -> Any:
    disc = model.disc
    output = disc.maxpool(disc.relu(disc.bn1(disc.conv1(tensor))))
    output = disc.layer1(output)
    output = disc.layer2(output)
    output = disc.layer3(output)
    output = disc.layer4(output)
    return disc.avgpool(output).flatten(1)


def _choose_device(torch: Any) -> str:
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _score_stats(scores: list[float]) -> dict[str, float | int | None]:
    if not scores:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(scores),
        "min": min(scores),
        "median": statistics.median(scores),
        "mean": statistics.mean(scores),
        "max": max(scores),
    }


def _state_dict(checkpoint: Any) -> dict[str, Any]:
    if isinstance(checkpoint, dict):
        for key in ("state_dict", "model_state_dict", "model"):
            candidate = checkpoint.get(key)
            if isinstance(candidate, dict):
                checkpoint = candidate
                break
    if not isinstance(checkpoint, dict):
        raise RuntimeError("Invalid LOTA checkpoint: expected a state dict")
    return {key.removeprefix("module."): value for key, value in checkpoint.items()}


def main() -> None:
    import torch
    from PIL import Image

    if not BASE_CHECKPOINT.is_file():
        raise SystemExit(f"LOTA checkpoint not found: {BASE_CHECKPOINT}")

    torch.manual_seed(SEED)
    random.seed(SEED)
    torch.set_num_threads(min(4, torch.get_num_threads()))
    device = torch.device(_choose_device(torch))

    manifest = _read_jsonl(DATASET_DIR / "manifest.jsonl")
    annotations = _read_jsonl(DATASET_DIR / "human_annotations.jsonl")
    manifest_by_id = {row["sample_id"]: row for row in manifest}
    annotation_by_id = {row["sample_id"]: row for row in annotations}
    if set(manifest_by_id) != set(annotation_by_id):
        raise SystemExit("Manifest and human annotation IDs do not match")

    # Positive training labels require source disclosure. The visual-only AI
    # judgment for xhs-039 is kept out of training as a weak-label probe.
    positive_ids = sorted(
        sample_id
        for sample_id, row in manifest_by_id.items()
        if row.get("provenance_label") == "disclosed_ai"
        and row.get("ground_truth_basis") not in (None, "none", "")
    )
    negative_ids = sorted(
        sample_id
        for sample_id, row in annotation_by_id.items()
        if row.get("image_origin_assessment") == "likely_real"
        and row.get("local_edit_concern") == "none_noted"
        and row.get("asset_type") != "official_product_image"
    )
    weak_positive_probe_ids = sorted(
        sample_id
        for sample_id, row in annotation_by_id.items()
        if row.get("image_origin_assessment") == "likely_ai_generated"
        and sample_id not in positive_ids
    )
    if not positive_ids or len(negative_ids) < 10:
        raise SystemExit("Too few disclosed AI or likely-real samples for this pilot")

    negative_groups: dict[str, list[str]] = {}
    for sample_id in negative_ids:
        group_id = manifest_by_id[sample_id]["post_group_id"]
        negative_groups.setdefault(group_id, []).append(sample_id)
    group_ids = list(negative_groups)
    random.Random(SEED).shuffle(group_ids)
    validation_group_count = max(1, math.ceil(len(group_ids) * 0.2))
    validation_group_count = min(validation_group_count, len(group_ids) - 1)
    validation_groups = set(group_ids[:validation_group_count])
    train_negative_ids = sorted(
        sample_id
        for group_id, sample_ids in negative_groups.items()
        if group_id not in validation_groups
        for sample_id in sample_ids
    )
    validation_negative_ids = sorted(
        sample_id
        for group_id, sample_ids in negative_groups.items()
        if group_id in validation_groups
        for sample_id in sample_ids
    )
    train_ids = positive_ids + train_negative_ids

    model = _build_lota_model(torch)
    checkpoint = torch.load(BASE_CHECKPOINT, map_location="cpu", weights_only=True)
    model.load_state_dict(_state_dict(checkpoint), strict=True)
    model.to(device).eval()

    all_ids = sorted(set(train_ids + validation_negative_ids + weak_positive_probe_ids))
    feature_by_id: dict[str, Any] = {}
    with torch.inference_mode():
        for sample_id in all_ids:
            image_path = Path(manifest_by_id[sample_id]["image_path"])
            if not image_path.is_absolute():
                image_path = DATASET_DIR / image_path
            if not image_path.is_file():
                raise SystemExit(f"Image missing for {sample_id}: {image_path}")
            with Image.open(image_path) as image:
                tensor = _preprocess(image.convert("RGB"), torch, seed=42)
            feature = _features(model, tensor.unsqueeze(0).to(device))
            feature_by_id[sample_id] = feature.detach().cpu()

    train_features = torch.cat([feature_by_id[sid] for sid in train_ids], dim=0).to(device)
    # LOTA's original scalar logit is a real-image logit: 1=real, 0=AI.
    train_targets = torch.tensor(
        [0.0 if sid in positive_ids else 1.0 for sid in train_ids],
        dtype=torch.float32,
        device=device,
    )
    n_ai = len(positive_ids)
    n_real = len(train_negative_ids)
    sample_weights = torch.where(
        train_targets == 1.0,
        torch.tensor(len(train_ids) / (2 * n_real), device=device),
        torch.tensor(len(train_ids) / (2 * n_ai), device=device),
    )

    fc = model.disc.fc
    original_weight = fc.weight.detach().clone()
    original_bias = fc.bias.detach().clone()
    optimizer = torch.optim.Adam(fc.parameters(), lr=LEARNING_RATE)
    bce = torch.nn.BCEWithLogitsLoss(reduction="none")
    loss_history: list[float] = []
    for _ in range(EPOCHS):
        optimizer.zero_grad(set_to_none=True)
        logits = fc(train_features).reshape(-1)
        data_loss = (bce(logits, train_targets) * sample_weights).mean()
        anchor = (fc.weight - original_weight).pow(2).sum()
        anchor += (fc.bias - original_bias).pow(2).sum()
        loss = data_loss + ANCHOR_STRENGTH * anchor
        loss.backward()
        optimizer.step()
        loss_history.append(float(loss.detach().cpu()))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(
        {key: value.detach().cpu() for key, value in model.state_dict().items()},
        EXPERIMENT_CHECKPOINT,
    )

    # Record both base and tuned signals. The training positives are in-sample;
    # the only separate AI-like probe is a subjective visual label, not truth.
    report_ids = sorted(set(train_ids + validation_negative_ids + weak_positive_probe_ids))
    results = []
    with torch.inference_mode():
        for sample_id in report_ids:
            features = feature_by_id[sample_id].to(device)
            base_logit = torch.nn.functional.linear(features, original_weight, original_bias).reshape(-1)[0]
            tuned_logit = fc(features).reshape(-1)[0]
            results.append({
                "sample_id": sample_id,
                "post_group_id": manifest_by_id[sample_id]["post_group_id"],
                "annotation": annotation_by_id[sample_id]["image_origin_assessment"],
                "training_role": (
                    "train_source_disclosed_ai"
                    if sample_id in positive_ids
                    else "train_likely_real"
                    if sample_id in train_negative_ids
                    else "held_out_likely_real_group"
                    if sample_id in validation_negative_ids
                    else "weak_ai_visual_probe"
                ),
                "lota_base_score": float(torch.sigmoid(-base_logit).cpu()),
                "lota_tuned_score": float(torch.sigmoid(-tuned_logit).cpu()),
            })

    def scores_for(role: str, key: str) -> list[float]:
        return [row[key] for row in results if row["training_role"] == role]

    probe_rank = None
    heldout_real_scores = scores_for("held_out_likely_real_group", "lota_tuned_score")
    weak_probe_scores = scores_for("weak_ai_visual_probe", "lota_tuned_score")
    if heldout_real_scores and weak_probe_scores:
        probe_rank = {
            "weak_positive_score": weak_probe_scores[0],
            "held_out_real_count": len(heldout_real_scores),
            "held_out_real_scores_at_or_below_probe": sum(
                score <= weak_probe_scores[0] for score in heldout_real_scores
            ),
            "rank_percentile": sum(score <= weak_probe_scores[0] for score in heldout_real_scores)
            / len(heldout_real_scores),
            "interpretation": "exploratory only; xhs-039 is a subjective visual label, not verified AI provenance",
        }

    roles = (
        "train_source_disclosed_ai",
        "train_likely_real",
        "held_out_likely_real_group",
        "weak_ai_visual_probe",
    )
    score_summary = {
        role: {
            "base": _score_stats(scores_for(role, "lota_base_score")),
            "fine_tuned": _score_stats(scores_for(role, "lota_tuned_score")),
        }
        for role in roles
    }
    held_out_above_unvalidated_half = sum(score >= 0.5 for score in heldout_real_scores)

    report = {
        "experiment": "LOTA beauty head-only fine-tuning pilot",
        "status": "experimental_not_for_production",
        "base_model": "LOTA ResNet-50 / provided SD1.5 scaling patch32 checkpoint",
        "fine_tuned_parameters": "final fully connected layer only; backbone and BatchNorm frozen",
        "device": str(device),
        "preprocessing": "same deterministic LOTA low-bit scaling and max-gradient patch as inference; seed=42",
        "training": {
            "seed": SEED,
            "epochs": EPOCHS,
            "learning_rate": LEARNING_RATE,
            "anchor_strength": ANCHOR_STRENGTH,
            "loss": "class-balanced BCE(real-logit) + L2 anchor to original classifier head",
            "train_positive_count": len(positive_ids),
            "train_positive_groups": sorted({manifest_by_id[sid]["post_group_id"] for sid in positive_ids}),
            "train_likely_real_count": len(train_negative_ids),
            "train_likely_real_groups": sorted({manifest_by_id[sid]["post_group_id"] for sid in train_negative_ids}),
            "held_out_likely_real_count": len(validation_negative_ids),
            "held_out_likely_real_groups": sorted(validation_groups),
            "weak_positive_probe_ids": weak_positive_probe_ids,
            "loss_first": loss_history[0],
            "loss_last": loss_history[-1],
            "loss_min": min(loss_history),
        },
        "score_semantics": "uncalibrated score, not a probability; do not use as production risk threshold",
        "score_summary_by_role": score_summary,
        "held_out_likely_real_above_0_5_diagnostic_only": {
            "count": held_out_above_unvalidated_half,
            "total": len(heldout_real_scores),
            "note": "0.5 is shown only to compare raw outputs; it is not a validated decision threshold",
        },
        "weak_positive_probe": probe_rank,
        "results": results,
        "not_fine_tuned": {
            "NFA-ViT": "requires pixel-level localization masks or source/edit pairs; none are available",
            "TruFor": "local-manipulation labels/masks are not available; global AI-generation labels do not match its target",
        },
        "limitations": [
            "Only six source-disclosed AI images are available for training, all from one post/source group.",
            "Likely-real training labels are visual judgments without verified capture provenance.",
            "xhs-039 is held out as a weak visual-only probe and cannot establish positive generalization.",
            "The held-out set cannot support reliable accuracy, calibration, or deployment-threshold estimates.",
            "This checkpoint is isolated under data/model-experiments and does not replace the production LOTA weights.",
        ],
        "checkpoint_path": str(EXPERIMENT_CHECKPOINT.relative_to(ROOT)),
    }
    (OUTPUT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with (OUTPUT_DIR / "scores.jsonl").open("w") as output_file:
        for row in results:
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(json.dumps({
        "experiment": report["experiment"],
        "status": report["status"],
        "device": report["device"],
        "train_positive_count": len(positive_ids),
        "train_likely_real_count": len(train_negative_ids),
        "held_out_likely_real_count": len(validation_negative_ids),
        "weak_positive_probe": probe_rank,
        "loss_first": loss_history[0],
        "loss_last": loss_history[-1],
        "checkpoint_path": report["checkpoint_path"],
        "report_path": str((OUTPUT_DIR / "report.json").relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
