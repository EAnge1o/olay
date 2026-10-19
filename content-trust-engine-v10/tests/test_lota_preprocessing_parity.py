"""Regression check against LOTA's upstream evaluation preprocessing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_official_loader():
    lota_dir = ROOT / "models/LOTA"
    sys.path.insert(0, str(lota_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            "_lota_upstream_loader", lota_dir / "loader.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(lota_dir))


def test_product_preprocess_matches_official_lota_evaluation_pipeline():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("cv2")
    import numpy as np
    from PIL import Image

    from content_trust.providers.lota import _preprocess

    official_loader = _load_official_loader()
    options = SimpleNamespace(
        isPatch=True,
        img_height=256,
        bit_mode="scaling",
        patch_size=32,
        patch_mode="max",
    )
    pixels = np.random.default_rng(1701).integers(
        0, 256, size=(192, 224, 3), dtype=np.uint8
    )
    image = Image.fromarray(pixels)

    # Upstream evaluation calls set_random_seed(42) before sampling RandomCrop.
    torch.manual_seed(42)
    official_tensor = official_loader.apply_preprocessing(image, options)
    product_tensor = _preprocess(image, torch, seed=42)

    torch.testing.assert_close(product_tensor, official_tensor, rtol=0, atol=1e-7)
