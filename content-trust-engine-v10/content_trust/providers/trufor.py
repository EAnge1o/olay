"""Optional lazy adapter for the local upstream TruFor inference code."""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import sys
import time
import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from content_trust.schemas import TruForSignal


class TruForLocalClient:
    provider_name = "trufor_local"

    def __init__(
        self,
        repo_path: str,
        checkpoint_path: str,
        *,
        device: str = "cpu",
    ) -> None:
        self._repo_path = Path(repo_path).expanduser().resolve()
        self._checkpoint_path = Path(checkpoint_path).expanduser().resolve()
        self._device_name = device
        self._model = None
        self._torch = None
        self._functional = None

    def analyze_image(
        self, *, image_path: str | None = None, image_url: str | None = None
    ) -> TruForSignal:
        if image_path is None:
            return TruForSignal(
                available=False,
                error="local_file_required",
            )
        path = Path(image_path).expanduser()
        if not path.is_file():
            return TruForSignal(available=False, error="image_unavailable")
        if not self._checkpoint_path.is_file():
            return TruForSignal(available=False, error="checkpoint_missing")
        if not self._repo_path.is_dir():
            return TruForSignal(available=False, error="trufor_source_missing")

        try:
            self._ensure_loaded()
            import numpy as np
            from PIL import Image

            image = np.asarray(Image.open(path).convert("RGB"))
            tensor = (
                self._torch.from_numpy(image.transpose(2, 0, 1).copy())
                .float()
                .unsqueeze(0)
                .to(self._device)
                / 256.0
            )
            started = time.perf_counter()
            with self._torch.inference_mode():
                prediction, reliability, detection, _ = self._model(
                    tensor, save_np=False
                )
                localization = self._functional.softmax(
                    prediction.squeeze(0), dim=0
                )[1].detach().cpu().numpy()
                reliability_map = self._torch.sigmoid(
                    reliability.squeeze(0)
                )[0].detach().cpu().numpy()
                detection_score = float(self._torch.sigmoid(detection).item())
            inference_device = self._device.type
            localization_score = _top_fraction_mean(localization, 0.01)
            reliability_mean = float(reliability_map.mean())
            map_path, overlay_path = _save_localization_artifacts(
                path,
                np.asarray(localization, dtype=np.float32),
            )
            return TruForSignal(
                available=True,
                detection_score=detection_score,
                localization_score=localization_score,
                reliability_mean=reliability_mean,
                localization_map_path=map_path,
                overlay_path=overlay_path,
                inference_device=inference_device,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except ImportError as exc:
            return TruForSignal(
                available=False,
                error=(
                    "trufor_dependency_missing:"
                    + (exc.name or "unknown").replace(".", "_")
                ),
            )
        except Exception as exc:
            code = getattr(exc, "code", None) or f"trufor_error_{type(exc).__name__.lower()}"
            return TruForSignal(available=False, error=code)

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        import torch.nn.functional as functional
        import numpy as np

        if self._repo_path.as_posix() not in sys.path:
            sys.path.insert(0, self._repo_path.as_posix())
        previous_cwd = Path.cwd()
        try:
            os.chdir(self._repo_path)
            config_module = importlib.import_module("lib.config")
            utils_module = importlib.import_module("lib.utils")
            device = _select_device(torch, self._device_name)
            args = SimpleNamespace(
                experiment="trufor_ph3",
                gpu=-1 if device.type == "cpu" else 0,
                opts=["TEST.MODEL_FILE", str(self._checkpoint_path)],
            )
            config_module.update_config(config_module.config, args)
            model = utils_module.get_model(config_module.config)
            # Load only tensor/primitive checkpoint contents. Never fall back to
            # unrestricted pickle loading when a checkpoint is rejected.
            with torch.serialization.safe_globals(
                [
                    argparse.Namespace,
                    np.core.multiarray.scalar,
                    np.dtype,
                    type(np.dtype(np.float64)),
                ]
            ):
                checkpoint = torch.load(
                    self._checkpoint_path,
                    map_location=device,
                    weights_only=True,
                )
            model.load_state_dict(checkpoint["state_dict"])
            model.to(device).eval()
        finally:
            os.chdir(previous_cwd)
        self._torch = torch
        self._functional = functional
        self._device = device
        self._model = model


def _select_device(torch: Any, requested: str):
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is not None and mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        return torch.device("cpu")
    return torch.device(requested)


def _top_fraction_mean(array, fraction: float) -> float:
    import numpy as np

    values = np.asarray(array, dtype=np.float32).reshape(-1)
    count = max(1, int(math.ceil(values.size * fraction)))
    return float(np.partition(values, values.size - count)[-count:].mean())


def _save_localization_artifacts(image_path: Path, localization):
    import numpy as np
    from PIL import Image

    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()[:16]
    output_dir = Path(os.getenv("TRUFOR_ARTIFACT_DIR", "/tmp/content-trust/trufor"))
    output_dir.mkdir(parents=True, exist_ok=True)
    map_path = output_dir / f"{digest}-localization.png"
    overlay_path = output_dir / f"{digest}-overlay.png"

    score = np.clip(localization, 0.0, 1.0)
    heat = np.zeros((*score.shape, 4), dtype=np.uint8)
    heat[..., 0] = 255
    heat[..., 1] = np.asarray(255 * (1.0 - score), dtype=np.uint8)
    heat[..., 2] = 0
    heat[..., 3] = np.asarray(170 * score, dtype=np.uint8)
    Image.fromarray(np.asarray(255 * score, dtype=np.uint8), mode="L").save(map_path)
    base = Image.open(image_path).convert("RGBA")
    overlay = Image.fromarray(heat, mode="RGBA").resize(base.size, Image.Resampling.BILINEAR)
    Image.alpha_composite(base, overlay).convert("RGB").save(overlay_path)
    return str(map_path), str(overlay_path)
