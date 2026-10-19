"""Run one local image detector at a time for model-swap experiments."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def _collect_images(paths: list[Path]) -> list[Path]:
    images: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            images.append(path)
        elif path.is_dir():
            images.extend(
                child
                for child in sorted(path.rglob("*"))
                if child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES
            )
    return images


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score images with one selected detector (no model fusion)."
    )
    parser.add_argument("--model", choices=("lota", "ferretnet"), required=True)
    parser.add_argument("paths", nargs="+", type=Path, help="image file(s) or folder(s)")
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args()

    images = _collect_images(args.paths)
    if not images:
        parser.error("No supported image files found in the supplied paths.")

    from content_trust.config import Settings
    from content_trust.providers.lota import LotaLocalClient
    from content_trust.providers.ferretnet import FerretNetLocalClient

    settings = Settings.from_env()
    if args.model == "lota":
        provider = LotaLocalClient(
            settings.lota_weights_path,
            device=settings.lota_device,
            timeout_seconds=settings.timeout_seconds,
        )
        model_name = "LOTA"
    else:
        provider = FerretNetLocalClient(
            settings.ferretnet_weights_path,
            repo_path=settings.ferretnet_repo_path,
            device=settings.ferretnet_device,
            timeout_seconds=settings.timeout_seconds,
        )
        model_name = "FerretNet-B-Median-3"

    records: list[dict[str, Any]] = []
    failures = 0
    for index, image_path in enumerate(images, start=1):
        print(f"[{index}/{len(images)}] {args.model}: {image_path}", file=sys.stderr)
        started = time.perf_counter()
        signal = provider.analyze_image(image_path=str(image_path))
        records.append(
            {
                "image_path": str(image_path),
                **signal.model_dump(mode="json"),
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            }
        )
        if not signal.available:
            failures += 1

    output = {
        "experiment": model_name,
        "status": "experimental_unvalidated_on_beauty_images",
        "score_note": "Uncalibrated model signal; not a probability.",
        "model_fusion": False,
        "image_count": len(records),
        "results": records,
    }
    output_json = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_json + "\n", encoding="utf-8")
        print(f"Saved {args.output}", file=sys.stderr)
    print(output_json)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
