"""Optional local FerretNet adapter for experimental AI-image scoring.

The network definition and local-pixel-dependency implementation are loaded
from the official FerretNet checkout at runtime. This keeps the experiment
aligned with upstream code without copying or maintaining a fork here.
"""

from __future__ import annotations

import argparse
import importlib.util
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from content_trust.schemas import AIGenerationSignal


class FerretNetLocalClient:
    """Score images with the official FerretNet-B-Median-3 checkpoint."""

    provider_name = "ferretnet_local"
    crop_size = 256

    def __init__(
        self,
        weights_path: str = "models/FerretNet/checkpoints/4cls_ckpt/ferretnet-b-median-3.pth",
        *,
        repo_path: str = "models/FerretNet",
        device: str = "auto",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._weights_path = _resolve_path(weights_path)
        self._repo_path = _resolve_path(repo_path)
        self._requested_device = device
        self._timeout = timeout_seconds
        self._model: Any | None = None
        self._torch: Any | None = None
        self._device: str | None = None

    def analyze_image(
        self, *, image_path: str | None = None, image_url: str | None = None
    ) -> AIGenerationSignal:
        if (image_path is None) == (image_url is None):
            return self._unavailable("image_source_required")
        if not self._weights_path.is_file():
            return self._unavailable("checkpoint_not_found")
        if not (self._repo_path / "src" / "model" / "ferretnet.py").is_file():
            return self._unavailable("source_not_found")

        try:
            image = self._load_image(image_path=image_path, image_url=image_url)
            model, torch, device = self._load_model()
            tensor = _preprocess(image, torch).unsqueeze(0).to(device)
            try:
                with torch.inference_mode():
                    score = float(torch.sigmoid(model(tensor)).reshape(-1)[0].item())
            except Exception:
                if device != "mps" or self._requested_device != "auto":
                    raise
                device = "cpu"
                model.to(device)
                self._device = device
                tensor = tensor.cpu()
                with torch.inference_mode():
                    score = float(torch.sigmoid(model(tensor)).reshape(-1)[0].item())

            return AIGenerationSignal(
                provider=self.provider_name,
                score=max(0.0, min(1.0, score)),
                available=True,
                score_semantics=(
                    "FerretNet synthetic-class sigmoid score; uncalibrated model "
                    "signal, not a probability"
                ),
                inference_device=device,
            )
        except Exception as exc:
            return self._unavailable(_error_code(exc))

    def _load_model(self) -> tuple[Any, Any, str]:
        if self._model is not None and self._torch is not None and self._device:
            return self._model, self._torch, self._device
        try:
            import torch
        except ImportError:
            raise RuntimeError("dependency_missing") from None

        model_source = self._repo_path / "src" / "model" / "ferretnet.py"
        lpd_source = self._repo_path / "src" / "model" / "lpd.py"
        if not model_source.is_file() or not lpd_source.is_file():
            raise RuntimeError("source_not_found")

        lpd_module = _load_module("content_trust_ferretnet_lpd", lpd_source)
        model_module = _load_module("content_trust_ferretnet_model", model_source)
        model = model_module.Ferret(
            in_channels=3,
            num_classes=1,
            dim=96,
            depths=[2, 2],
            lpd_func="median",
            window_size=3,
            lpd_dict=lpd_module.get_lpd_dict(),
        )

        checkpoint = _load_checkpoint(torch, self._weights_path)
        if not isinstance(checkpoint, dict):
            raise RuntimeError("invalid_checkpoint")
        for key in ("model", "state_dict", "model_state_dict"):
            if isinstance(checkpoint.get(key), dict):
                checkpoint = checkpoint[key]
                break
        if not checkpoint or not all(
            isinstance(key, str) and isinstance(value, torch.Tensor)
            for key, value in checkpoint.items()
        ):
            raise RuntimeError("invalid_checkpoint")

        state = {key.removeprefix("module."): value for key, value in checkpoint.items()}
        model_state = model.state_dict()
        compatible_state = {
            key: value
            for key, value in state.items()
            if key in model_state and tuple(value.shape) == tuple(model_state[key].shape)
        }
        if not compatible_state:
            raise RuntimeError("invalid_checkpoint")
        load_result = model.load_state_dict(compatible_state, strict=False)
        if load_result.missing_keys:
            raise RuntimeError("invalid_checkpoint")

        device = _select_device(torch, self._requested_device)
        model.to(device)
        model.eval()
        self._model, self._torch, self._device = model, torch, device
        return model, torch, device

    def _load_image(
        self, *, image_path: str | None, image_url: str | None
    ) -> Any:
        try:
            from PIL import Image
        except ImportError:
            raise RuntimeError("dependency_missing") from None

        if image_path is not None:
            path = Path(image_path)
            if not path.is_file():
                raise RuntimeError("image_unavailable")
            with path.open("rb") as image_file:
                return Image.open(image_file).convert("RGB")

        response = None
        last_error: Exception | None = None
        for _ in range(2):
            try:
                candidate = httpx.get(
                    image_url,
                    timeout=self._timeout,
                    follow_redirects=True,
                )
                candidate.raise_for_status()
                response = candidate
                break
            except Exception as exc:
                last_error = exc
        if response is None and last_error is not None:
            raise last_error
        return Image.open(BytesIO(response.content)).convert("RGB")

    def _unavailable(self, error: str) -> AIGenerationSignal:
        return AIGenerationSignal(
            provider=self.provider_name,
            available=False,
            score=None,
            error=error,
        )


def _resolve_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    project_root = Path(__file__).resolve().parents[2]
    return project_root / candidate


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("source_not_found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_checkpoint(torch: Any, path: Path) -> Any:
    """Load tensor weights safely, allowing the basic Namespace in official ckpts."""
    safe_globals = getattr(torch.serialization, "safe_globals", None)
    context = safe_globals([argparse.Namespace]) if safe_globals else nullcontext()
    try:
        with context:
            return torch.load(path, map_location="cpu", weights_only=True)
    except Exception:
        # Do not fall back to unrestricted pickle loading for model files.
        raise RuntimeError("invalid_checkpoint") from None


def _select_device(torch: Any, requested: str) -> str:
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("device_unavailable")
        return "cuda"
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    mps_available = bool(mps and mps.is_available())
    if requested == "mps":
        if not mps_available:
            raise RuntimeError("device_unavailable")
        return "mps"
    if requested != "auto":
        raise RuntimeError("invalid_device")
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if mps_available else "cpu"


def _preprocess(image: Any, torch: Any) -> Any:
    """Match the official Test.yaml 256px center-crop evaluation transform."""
    from PIL import ImageOps
    import numpy as np

    crop_size = FerretNetLocalClient.crop_size
    width, height = image.size
    if width < crop_size or height < crop_size:
        pad_left = round(max(0, crop_size - width) / 2.0)
        pad_top = round(max(0, crop_size - height) / 2.0)
        pad_right = max(0, crop_size - width - pad_left)
        pad_bottom = max(0, crop_size - height - pad_top)
        image = ImageOps.expand(
            image,
            border=(pad_left, pad_top, pad_right, pad_bottom),
            fill=0,
        )
        width, height = image.size

    left = int(round((width - crop_size) / 2.0))
    top = int(round((height - crop_size) / 2.0))
    image = image.crop((left, top, left + crop_size, top + crop_size))
    array = np.asarray(image, dtype=np.uint8).copy()
    tensor = torch.from_numpy(array).permute(2, 0, 1).float().div_(255.0)
    mean = tensor.new_tensor((0.48145466, 0.4578275, 0.40821073)).view(3, 1, 1)
    std = tensor.new_tensor((0.26862954, 0.26130258, 0.27577711)).view(3, 1, 1)
    return (tensor - mean) / std


def _error_code(exc: Exception) -> str:
    known_codes = {
        "checkpoint_not_found",
        "dependency_missing",
        "device_unavailable",
        "image_source_required",
        "image_unavailable",
        "invalid_checkpoint",
        "invalid_device",
        "source_not_found",
    }
    message = str(exc)
    if message in known_codes:
        return message
    if isinstance(exc, ImportError):
        return "dependency_missing"
    return "provider_error"
