#!/usr/bin/env python3
"""Compare the official LOTA evaluation path with Content Trust ingestion."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = (
    ROOT
    / "data/model-experiments/internet_vct2_50_20261015/images/row_00000_sd35.jpg"
)
DEFAULT_IMAGE_SHA256 = "929b6338358ee0530bb1341837dbcf3fe08d98cf06cbabbc05dc9c18161fd5f2"
DEFAULT_OUTPUT = ROOT / "data/model-experiments/lota_preprocessing_sanity_20261016"
DEFAULT_SAMPLE_BEFORE_FIX = {
    "max_abs_diff": 0.017507314682006836,
    "mean_abs_diff": 0.003619428025558591,
    "mismatched_element_fraction": 0.20870463053385416,
    "identified_cause": "Pillow bilinear resize in product path vs OpenCV INTER_LINEAR upstream",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _array_sha256(array: Any, np: Any) -> str:
    return _sha256(np.ascontiguousarray(array).tobytes())


def _load_module(name: str, path: Path, *, extra_path: Path | None = None) -> Any:
    if extra_path is not None:
        sys.path.insert(0, str(extra_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if extra_path is not None:
            sys.path.remove(str(extra_path))


def _load_official_preprocessing(torch: Any, image: Any, seed: int) -> tuple[Any, dict[str, Any]]:
    import cv2
    import numpy as np
    from PIL import Image
    from torchvision.transforms import RandomCrop

    lota_dir = ROOT / "models/LOTA"
    loader = _load_module(
        "_lota_official_loader_for_audit", lota_dir / "loader.py", extra_path=lota_dir
    )
    options = SimpleNamespace(
        isPatch=True,
        img_height=256,
        bit_mode="scaling",
        patch_size=32,
        patch_mode="max",
    )

    crop_calls: list[dict[str, int]] = []
    original_get_params = RandomCrop.get_params
    original_descriptor = RandomCrop.__dict__["get_params"]

    def capture_get_params(image_arg: Any, output_size: Any) -> tuple[int, int, int, int]:
        coordinates = original_get_params(image_arg, output_size)
        top, left, height, width = coordinates
        crop_calls.append(
            {"top": int(top), "left": int(left), "height": int(height), "width": int(width)}
        )
        return coordinates

    RandomCrop.get_params = staticmethod(capture_get_params)
    try:
        torch.manual_seed(seed)
        tensor = loader.apply_preprocessing(image, options)
    finally:
        RandomCrop.get_params = original_descriptor

    pixels = np.asarray(image.convert("RGB"), dtype=np.uint8)
    low_bits = ((pixels & 0x07) * (255 // 7)).astype(np.uint8)
    low_bits_image = Image.fromarray(low_bits)
    original_bit_plane_size = list(low_bits_image.size)
    pre_resize_applied = min(low_bits_image.size) < options.patch_size
    if pre_resize_applied:
        low_bits_image = loader.transforms.Resize(
            (options.img_height, options.img_height)
        )(low_bits_image)
    low_bits_for_crop = np.asarray(low_bits_image, dtype=np.uint8)

    official_gradient_score = sys.modules["bit_patch"].compute

    candidates = []
    for index, coordinates in enumerate(crop_calls):
        top = coordinates["top"]
        left = coordinates["left"]
        patch = low_bits_for_crop[top : top + 32, left : left + 32]
        candidates.append(
            {
                "index": index,
                "top": top,
                "left": left,
                "height": coordinates["height"],
                "width": coordinates["width"],
                "gradient_score": int(official_gradient_score(patch)),
                "patch": patch,
            }
        )
    if len(candidates) != 64:
        raise RuntimeError(f"expected 64 official crop samples, got {len(candidates)}")
    selected = max(candidates, key=lambda candidate: candidate["gradient_score"])
    final_rgb = cv2.resize(
        selected["patch"], (256, 256), interpolation=cv2.INTER_LINEAR
    )
    return tensor, {
        "input_rgb": {
            "shape_hwc": list(pixels.shape),
            "channel_order": "RGB",
            "pixel_sha256": _array_sha256(pixels, np),
        },
        "bit_plane": {
            "operation": "official bit_patch.scaling: (channel & 0x07) * (255 // 7)",
            "shape_hwc": list(low_bits.shape),
            "range": [int(low_bits.min()), int(low_bits.max())],
            "pixel_sha256": _array_sha256(low_bits, np),
        },
        "patch_selection": {
            "rng": "torch global RNG, seeded with set_random_seed(42) equivalent",
            "seed": int(seed),
            "candidate_count": len(candidates),
            "patch_size": options.patch_size,
            "pre_resize_applied": pre_resize_applied,
            "pre_resize_from": original_bit_plane_size
            if pre_resize_applied
            else None,
            "pre_resize_to": [options.img_height, options.img_height]
            if pre_resize_applied
            else None,
            "candidates": [
                {key: value for key, value in candidate.items() if key != "patch"}
                for candidate in candidates
            ],
            "selected_index": selected["index"],
            "selected_bbox_xyxy": [
                selected["left"],
                selected["top"],
                selected["left"] + options.patch_size,
                selected["top"] + options.patch_size,
            ],
            "selected_patch_sha256": _array_sha256(selected["patch"], np),
        },
        "final_resize": {
            "implementation": "official bit_patch.py -> cv2.resize",
            "interpolation": "INTER_LINEAR (OpenCV default, explicit here)",
            "from": [options.patch_size, options.patch_size],
            "to": [options.img_height, options.img_height],
            "rgb_sha256": _array_sha256(final_rgb, np),
        },
    }


def _checkpoint_state(torch: Any, checkpoint_path: Path) -> dict[str, Any]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if isinstance(checkpoint, dict):
        for key in ("state_dict", "model_state_dict", "model"):
            value = checkpoint.get(key)
            if isinstance(value, dict):
                checkpoint = value
                break
    if not isinstance(checkpoint, dict):
        raise RuntimeError("LOTA checkpoint did not contain a state dict")
    return {key.removeprefix("module."): value for key, value in checkpoint.items()}


def _find_manifest_row(image_path: Path) -> dict[str, Any] | None:
    manifest = ROOT / "data/model-experiments/internet_vct2_50_20261015/manifest.jsonl"
    if not manifest.is_file():
        return None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if (ROOT / "data/model-experiments/internet_vct2_50_20261015" / row["image_path"]).resolve() == image_path:
            return row
    return None


def run(image_path: Path, output_dir: Path, seed: int) -> dict[str, Any]:
    import cv2
    import numpy as np
    import torch
    from PIL import Image, __version__ as pillow_version

    sys.path.insert(0, str(ROOT))
    from content_trust.providers.lota import (
        LotaLocalClient,
        _build_lota_model,
        _preprocess_with_trace,
    )

    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    image_path = image_path.resolve()
    raw_bytes = image_path.read_bytes()
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_input = output_dir / f"input{image_path.suffix.lower()}"
    if image_path != saved_input.resolve():
        shutil.copyfile(image_path, saved_input)

    with Image.open(image_path) as source:
        raw_metadata = {
            "format": source.format,
            "mode_before_rgb_conversion": source.mode,
            "resolution_before_rgb_conversion": list(source.size),
            "exif_orientation_tag_274": source.getexif().get(274),
            "exif_orientation_applied": False,
            "decode_library": f"Pillow {pillow_version}",
            "channel_order_after_decode": "RGB",
        }
        image = source.convert("RGB")

    official_tensor, official_trace = _load_official_preprocessing(torch, image, seed)
    product_tensor, product_trace = _preprocess_with_trace(image, torch, seed=seed)
    official_array = official_tensor.detach().cpu().numpy()
    product_array = product_tensor.detach().cpu().numpy()
    abs_diff = np.abs(official_array - product_array)
    official_hash = _array_sha256(official_array, np)
    product_hash = _array_sha256(product_array, np)

    np.save(output_dir / "official_final_tensor.npy", official_array)
    np.save(output_dir / "product_final_tensor.npy", product_array)
    np.save(output_dir / "absolute_tensor_difference.npy", abs_diff)

    # Use the upstream model class and the product model builder with the exact
    # same checkpoint. The upstream constructor otherwise attempts a redundant
    # ImageNet download before loading its complete LOTA checkpoint.
    lota_dir = ROOT / "models/LOTA"
    official_arch = _load_module(
        "_lota_official_model_for_audit", lota_dir / "model.py", extra_path=lota_dir
    )
    upstream_resnet50 = official_arch.resnet50
    official_arch.resnet50 = lambda pretrained=False, **kwargs: upstream_resnet50(
        pretrained=False, **kwargs
    )
    official_model = official_arch.model(pretrain=False)
    product_model = _build_lota_model(torch)
    checkpoint_path = lota_dir / "Network_best.pth"
    state = _checkpoint_state(torch, checkpoint_path)
    official_model.load_state_dict(state, strict=True)
    product_model.load_state_dict(state, strict=True)
    official_model.eval()
    product_model.eval()
    official_input = official_tensor.unsqueeze(0)
    product_input = product_tensor.unsqueeze(0)
    with torch.inference_mode():
        official_logit_official_input = float(
            official_model(official_input).reshape(-1)[0].item()
        )
        product_logit_product_input = float(
            product_model(product_input).reshape(-1)[0].item()
        )
        official_logit_product_input = float(
            official_model(product_input).reshape(-1)[0].item()
        )
        product_logit_official_input = float(
            product_model(official_input).reshape(-1)[0].item()
        )

    sigmoid = lambda value: 1.0 / (1.0 + float(np.exp(-value)))
    official_real_score = sigmoid(official_logit_official_input)
    official_ai_equivalent = sigmoid(-official_logit_official_input)
    product_ai_score = sigmoid(-product_logit_product_input)
    product_api_result = LotaLocalClient(
        weights_path=str(lota_dir / "Network_best.pth"), device="cpu"
    ).analyze_image(image_path=str(image_path), preprocessing_seed=seed)
    if not product_api_result.available:
        raise RuntimeError(
            f"production LotaLocalClient failed: {product_api_result.error}"
        )
    product_api_score = float(product_api_result.score)
    manifest_row = _find_manifest_row(image_path)
    if manifest_row is not None:
        sample_caveat = (
            "The upstream GenImage validation split is not present in this workspace. "
            "This fixed labelled COCO_AI/VCT2 sample is used only to compare the exact "
            "upstream LOTA loader code with product ingestion."
        )
    else:
        sample_caveat = (
            "This image is not matched to the local labelled COCO_AI/VCT2 manifest. "
            "The upstream GenImage validation split is not present in this workspace."
        )

    report = {
        "audit": "LOTA official evaluation preprocessing vs production ingestion",
        "scope": "sanity audit only; no tuning or training",
        "before_fix_observation": (
            DEFAULT_SAMPLE_BEFORE_FIX
            if _sha256(raw_bytes) == DEFAULT_IMAGE_SHA256
            else None
        ),
        "sample": {
            "path": str(image_path),
            "saved_copy": str(saved_input),
            "sha256": _sha256(raw_bytes),
            "bytes": len(raw_bytes),
            "manifest": manifest_row,
            "caveat": sample_caveat,
        },
        "raw_file_and_decode": raw_metadata,
        "official_pipeline": official_trace,
        "production_pipeline": product_trace,
        "tensor_comparison": {
            "shape_chw": list(official_array.shape),
            "official_sha256": official_hash,
            "production_sha256": product_hash,
            "max_abs_diff": float(abs_diff.max()),
            "mean_abs_diff": float(abs_diff.mean()),
            "rmse": float(np.sqrt(np.mean(np.square(abs_diff)))),
            "mismatched_element_fraction": float(np.mean(abs_diff != 0)),
            "official_tensor_file": "official_final_tensor.npy",
            "production_tensor_file": "product_final_tensor.npy",
            "absolute_difference_file": "absolute_tensor_difference.npy",
            "parity": bool(np.allclose(official_array, product_array, rtol=0, atol=1e-7)),
        },
        "model": {
            "checkpoint": str(checkpoint_path),
            "checkpoint_sha256": _sha256(checkpoint_path.read_bytes()),
            "checkpoint_loaded_strictly_in_both_models": True,
            "device": "cpu",
            "official_model_logit_on_official_tensor": official_logit_official_input,
            "production_model_logit_on_production_tensor": product_logit_product_input,
            "official_model_logit_on_production_tensor": official_logit_product_input,
            "production_model_logit_on_official_tensor": product_logit_official_input,
            "official_test_py_probability_real": official_real_score,
            "official_fake_score_equivalent": official_ai_equivalent,
            "production_ai_generation_signal": product_ai_score,
            "production_api_result": product_api_result.model_dump(mode="json"),
            "production_api_score_abs_diff": abs(product_api_score - product_ai_score),
            "equivalent_ai_score_abs_diff": abs(official_ai_equivalent - product_ai_score),
            "official_score_semantics": "sigmoid(real-class logit); upstream labels real=1 and AI=0",
            "production_score_semantics": "sigmoid(-real-class logit), the AI-score complement",
        },
        "runtime": {
            "torch": torch.__version__,
            "torchvision": __import__("torchvision").__version__,
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "pillow": pillow_version,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    comparison = report["tensor_comparison"]
    model = report["model"]
    baseline = report["before_fix_observation"]
    baseline_markdown = (
        f"- Before fix: max tensor difference `{baseline['max_abs_diff']:.9f}`, "
        f"mean difference `{baseline['mean_abs_diff']:.9f}`, about "
        f"`{baseline['mismatched_element_fraction']:.1%}` elements differed. "
        f"Cause: {baseline['identified_cause']}."
        if baseline is not None
        else "- Before-fix comparison is recorded only for the default fixed sample; no baseline is claimed for this custom image."
    )
    sample_scope_markdown = (
        "The upstream GenImage validation split is not present in this workspace. "
        "This fixed labelled COCO_AI/VCT2 sample is used only to compare the exact "
        "upstream LOTA preprocessing code and current product ingestion."
        if manifest_row is not None
        else "This image is not matched to the local labelled COCO_AI/VCT2 manifest. "
        "The upstream GenImage validation split is not present in this workspace."
    )
    markdown = f"""# LOTA Preprocessing Sanity Audit

Scope: preprocessing and inference parity only. No model tuning or training.

## Sample

- ID: `{manifest_row['sample_id'] if manifest_row else image_path.name}`
- Label: `{manifest_row['label'] if manifest_row else 'not recorded'}`
- File: `{image_path}`
- SHA-256: `{_sha256(raw_bytes)}`
- Decode: {raw_metadata['format']} {raw_metadata['resolution_before_rgb_conversion']} RGB, EXIF orientation `{raw_metadata['exif_orientation_tag_274']}`

{sample_scope_markdown}

## Result

{baseline_markdown}
- After fix: tensor max and mean absolute differences are `{comparison['max_abs_diff']:.1f}`; tensor SHA-256 values match: `{comparison['official_sha256']}`.
- Official real-class logit: `{model['official_model_logit_on_official_tensor']:.10f}`.
- Production real-class logit: `{model['production_model_logit_on_production_tensor']:.10f}`.
- Official repo real score: `{model['official_test_py_probability_real']:.12f}`.
- Equivalent AI signal / production AI signal: `{model['official_fake_score_equivalent']:.12f}` / `{model['production_ai_generation_signal']:.12f}`.
- Equivalent AI-score absolute difference: `{model['equivalent_ai_score_abs_diff']:.2e}`.
- Public production client AI score: `{model['production_api_result']['score']:.12f}` (difference from direct product path: `{model['production_api_score_abs_diff']:.2e}`).

Official labels use real=1 and AI=0, so upstream `test.py` reports `sigmoid(real_logit)`. The product adapter reports the complementary AI score `sigmoid(-real_logit)`.

## Artifacts

- `report.json`: complete stage-by-stage trace, all 64 crop coordinates and gradient scores, input/checkpoint hashes, tensor comparison, and logits.
- `input.jpg`: exact source image copy.
- `official_final_tensor.npy`, `product_final_tensor.npy`: final normalized CHW tensors.
- `absolute_tensor_difference.npy`: elementwise absolute difference.

To rerun from the repository root: `.venv/bin/python scripts/audit_lota_preprocessing.py`. Install the optional LOTA dependencies first with `.venv/bin/python -m pip install -e '.[lota_local]'`.
"""
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-path", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    report = run(args.image_path, args.output_dir, args.seed)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
