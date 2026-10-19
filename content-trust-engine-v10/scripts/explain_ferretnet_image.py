"""Create an experimental FerretNet Grad-CAM and LPD-residual explanation.

The outputs are model-attribution clues, not proof that an image is fake or edited.
Grad-CAM follows the method in the official FerretNet demo, without requiring
its optional torchvision/matplotlib visualization stack.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TILE_SIZE = 256


def _model_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    pad_left = max(0, (TILE_SIZE - width) // 2)
    pad_top = max(0, (TILE_SIZE - height) // 2)
    pad_right = max(0, TILE_SIZE - width - pad_left)
    pad_bottom = max(0, TILE_SIZE - height - pad_top)
    image = ImageOps.expand(
        image,
        border=(pad_left, pad_top, pad_right, pad_bottom),
        fill=(0, 0, 0),
    )
    width, height = image.size
    left = int(round((width - TILE_SIZE) / 2.0))
    top = int(round((height - TILE_SIZE) / 2.0))
    return image.crop((left, top, left + TILE_SIZE, top + TILE_SIZE))


def _colorize(values: np.ndarray) -> Image.Image:
    values = np.clip(values, 0.0, 1.0)
    red = np.clip(1.5 - np.abs(4.0 * values - 3.0), 0.0, 1.0)
    green = np.clip(1.5 - np.abs(4.0 * values - 2.0), 0.0, 1.0)
    blue = np.clip(1.5 - np.abs(4.0 * values - 1.0), 0.0, 1.0)
    rgb = np.stack((red, green, blue), axis=-1)
    return Image.fromarray(np.uint8(rgb * 255.0))


def _normalize_map(values: Any) -> np.ndarray:
    array = values.detach().float().cpu().numpy()
    low = float(array.min())
    high = float(array.max())
    if high - low < 1e-12:
        return np.zeros_like(array, dtype=np.float32)
    return ((array - low) / (high - low)).astype(np.float32)


def explain(image_path: Path, output_dir: Path) -> dict[str, Any]:
    from content_trust.config import Settings
    from content_trust.providers.ferretnet import FerretNetLocalClient, _preprocess

    settings = Settings.from_env()
    client = FerretNetLocalClient(
        settings.ferretnet_weights_path,
        repo_path=settings.ferretnet_repo_path,
        device=settings.ferretnet_device,
        timeout_seconds=settings.timeout_seconds,
    )
    model, torch, device = client._load_model()
    with Image.open(image_path) as opened:
        original = opened.convert("RGB")
    crop = _model_crop(original)
    input_tensor = _preprocess(crop, torch).unsqueeze(0).to(device)

    captured: dict[str, Any] = {}

    def capture_features(_module: Any, _inputs: Any, output: Any) -> None:
        captured["features"] = output
        output.register_hook(lambda gradient: captured.__setitem__("gradients", gradient))

    hook = model.feature.register_forward_hook(capture_features)
    try:
        model.zero_grad(set_to_none=True)
        logits = model(input_tensor)
        synthetic_logit = logits[0, 0]
        synthetic_score = float(torch.sigmoid(synthetic_logit.detach()).item())
        synthetic_logit.backward()
    finally:
        hook.remove()

    features = captured["features"].detach()
    gradients = captured["gradients"].detach()
    weights = gradients.mean(dim=(2, 3), keepdim=True)
    cam = torch.relu((weights * features).sum(dim=1, keepdim=True))
    cam = torch.nn.functional.interpolate(
        cam,
        size=(TILE_SIZE, TILE_SIZE),
        mode="bilinear",
        align_corners=False,
    )[0, 0]
    cam_map = _normalize_map(cam)

    with torch.no_grad():
        residual = (input_tensor - model.lpd(input_tensor)).abs().mean(dim=1)[0]
    residual_map = _normalize_map(residual)

    heatmap = _colorize(cam_map)
    cam_overlay = Image.blend(crop, heatmap, alpha=0.48)
    residual_visual = _colorize(residual_map)

    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "model_input_256.png"
    overlay_path = output_dir / "gradcam_overlay.png"
    residual_path = output_dir / "lpd_residual.png"
    crop.save(input_path)
    cam_overlay.save(overlay_path)
    residual_visual.save(residual_path)

    threshold = float(np.quantile(cam_map, 0.9))
    rows, columns = np.where(cam_map >= threshold)
    hotspot_bbox = [
        int(columns.min()),
        int(rows.min()),
        int(columns.max()) + 1,
        int(rows.max()) + 1,
    ]
    report = {
        "image_path": str(image_path.resolve()),
        "model": "FerretNet-B-Median-3",
        "inference_device": device,
        "model_input": "center crop or centered black padding, 256x256",
        "synthetic_class_signal": synthetic_score,
        "score_semantics": "uncalibrated synthetic-class sigmoid signal, not a probability",
        "reference_threshold_prediction": "ai_generated" if synthetic_score >= 0.5 else "real",
        "explanation_method": {
            "gradcam": {
                "target_layer": "model.feature",
                "hotspot_threshold": "top 10% of normalized Grad-CAM values",
                "hotspot_bbox_xyxy_on_model_input": hotspot_bbox,
                "overlay_path": overlay_path.name,
                "interpretation": "Highlights regions that most influenced the synthetic-class logit for this model input.",
            },
            "lpd_residual": {
                "visual_path": residual_path.name,
                "interpretation": "Absolute local median-reconstruction residual used by FerretNet before feature extraction.",
            },
        },
        "limitations": [
            "Grad-CAM is a coarse attribution map, not a pixel-level manipulation mask or proof of image generation.",
            "The LPD residual visualizes local pixel differences; natural edges and texture can also produce strong responses.",
            "This checkpoint and score have not been calibrated for beauty images or social-platform recompression.",
            "The visualization applies to the 256x256 center crop/padded input, not necessarily the entire source image.",
        ],
        "artifacts": {
            "model_input": input_path.name,
            "gradcam_overlay": overlay_path.name,
            "lpd_residual": residual_path.name,
        },
    }
    (output_dir / "explanation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Explain one image with FerretNet Grad-CAM and LPD residuals.")
    parser.add_argument("image", type=Path, help="input image path")
    parser.add_argument("--output-dir", type=Path, default=Path("data/model-experiments/ferretnet_explainer"))
    args = parser.parse_args()
    image_path = args.image.expanduser().resolve()
    if not image_path.is_file():
        parser.error(f"image not found: {image_path}")
    report = explain(image_path, args.output_dir.expanduser().resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
