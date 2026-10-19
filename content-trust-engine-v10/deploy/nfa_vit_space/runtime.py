"""Load and run the upstream NFA-ViT checkpoint for one image at a time."""

from __future__ import annotations

import argparse
import os
import sys
import types
import warnings
from contextlib import contextmanager
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

import httpx
import numpy as np
from huggingface_hub import snapshot_download
from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError
from PIL import Image


class NfaVitRuntime:
    INPUT_SIZE = 512
    MASK_THRESHOLD = 0.5

    def __init__(self) -> None:
        # NFA-ViT has a CPU fallback for individual unsupported MPS operators.
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import torch

        self._torch = torch
        self._device = _select_device(torch, os.getenv("NFA_VIT_DEVICE", "auto"))
        source_dir = Path(os.getenv("NFA_VIT_SOURCE_DIR", "/opt/BR-Gen"))
        model_dir = source_dir / "model_zoo" / "nfa_vit"
        if not model_dir.is_dir():
            raise RuntimeError(f"NFA-ViT source not found at {model_dir}")
        _install_registry_compatibility_shim()
        sys.path.insert(0, str(source_dir))

        weights_dir = _download_model_weights()
        required = {
            name: weights_dir / name
            for name in (
                "noiseprint.pth",
                "segformer_b0_backbone_weights.pth",
                "segformer_b2_backbone_weights.pth",
                "checkpoint-9999.pth",
            )
        }
        missing = [name for name, path in required.items() if not path.is_file()]
        if missing:
            raise RuntimeError("NFA-ViT checkpoint files missing: " + ", ".join(missing))

        # The upstream model_zoo/__init__.py imports NFA_ViT, but the checked
        # out NFA-ViT source defines NFA_ViT_modify1 instead. Load this model as
        # an isolated package so Python does not execute that stale __init__.
        NFA_ViT_modify1 = _load_upstream_model_class(model_dir)

        with _safe_torch_load_defaults(torch):
            self._model = NFA_ViT_modify1(
                np_pretrain_weights=str(required["noiseprint.pth"]),
                seg_b0_pretrain_weights=str(required["segformer_b0_backbone_weights.pth"]),
                seg_b2_pretrain_weights=str(required["segformer_b2_backbone_weights.pth"]),
            )
            checkpoint = torch.load(
                required["checkpoint-9999.pth"],
                map_location="cpu",
                weights_only=True,
            )
        state_dict = checkpoint.get("model", checkpoint)
        self._model.load_state_dict(state_dict, strict=True)
        try:
            self._model.to(self._device)
        except RuntimeError as exc:
            if self._device.type != "mps":
                raise
            warnings.warn(
                f"NFA-ViT could not load onto MPS; using CPU ({type(exc).__name__}).",
                RuntimeWarning,
                stacklevel=2,
            )
            self._device = torch.device("cpu")
            self._model.to(self._device)
        self._model.eval()

    def predict(self, image: Image.Image) -> tuple[dict, Image.Image]:
        torch = self._torch
        original = image.convert("RGB")
        resized = original.resize(
            (self.INPUT_SIZE, self.INPUT_SIZE), Image.Resampling.BILINEAR
        )
        array = np.asarray(resized, dtype=np.float32) / 255.0
        try:
            output = self._predict_tensor(array)
        except RuntimeError as exc:
            if self._device.type != "mps":
                raise
            warnings.warn(
                f"NFA-ViT MPS inference failed; retrying on CPU ({type(exc).__name__}).",
                RuntimeWarning,
                stacklevel=2,
            )
            try:
                torch.mps.empty_cache()
            except (AttributeError, RuntimeError):
                pass
            self._device = torch.device("cpu")
            self._model.to(self._device)
            output = self._predict_tensor(array)

        mask = output["pred_mask"][0, 0].float().cpu().numpy()
        # Ignore the upstream whole-image classification head. Derive the
        # exposed signal only from localized segmentation-mask probabilities.
        flat_mask = mask.reshape(-1)
        top_count = max(1, int(np.ceil(flat_mask.size * 0.01)))
        score = float(np.partition(flat_mask, -top_count)[-top_count:].mean())
        binary_mask = mask >= self.MASK_THRESHOLD
        region_fraction = float(binary_mask.mean())
        ys, xs = np.where(binary_mask)
        region_bbox = (
            {
                "x_min": float(xs.min() / self.INPUT_SIZE),
                "y_min": float(ys.min() / self.INPUT_SIZE),
                "x_max": float((xs.max() + 1) / self.INPUT_SIZE),
                "y_max": float((ys.max() + 1) / self.INPUT_SIZE),
            }
            if xs.size
            else None
        )
        overlay = _make_overlay(resized, binary_mask)
        if original.size != overlay.size:
            overlay = overlay.resize(original.size, Image.Resampling.BILINEAR)
        return (
            {
                "provider": "NFA-ViT",
                "local_manipulation_score": min(1.0, max(0.0, score)),
                "localized_region_fraction": min(1.0, max(0.0, region_fraction)),
                "localized_region_bbox": region_bbox,
                "localization_score_method": "top_1_percent_mean_of_localization_mask",
                "model_scope": "localized AI-generated/inpainted region detection",
                "input_size": [self.INPUT_SIZE, self.INPUT_SIZE],
                "score_semantics": "uncalibrated localization-mask signal, not a probability",
                "inference_device": self._device.type,
            },
            overlay,
        )

    def _predict_tensor(self, array: np.ndarray) -> dict:
        torch = self._torch
        tensor = (
            torch.from_numpy(array.transpose(2, 0, 1))
            .unsqueeze(0)
            .to(self._device)
        )
        dummy_mask = torch.zeros(
            (1, 1, self.INPUT_SIZE, self.INPUT_SIZE),
            dtype=tensor.dtype,
            device=self._device,
        )
        dummy_label = torch.zeros((1,), dtype=tensor.dtype, device=self._device)
        with torch.inference_mode():
            return self._model(tensor, dummy_mask, dummy_label)


def _make_overlay(image: Image.Image, mask: np.ndarray) -> Image.Image:
    source = np.asarray(image.convert("RGB"), dtype=np.float32)
    color = np.zeros_like(source)
    color[..., 0] = 255.0
    alpha = np.where(mask[..., None], 0.42, 0.0)
    blended = source * (1.0 - alpha) + color * alpha
    return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8), mode="RGB")


def _select_device(torch, requested: str):
    requested = requested.strip().lower()
    if requested not in {"auto", "cuda", "mps", "cpu"}:
        raise RuntimeError("NFA_VIT_DEVICE must be one of: auto, cuda, mps, cpu")
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("NFA_VIT_DEVICE=cuda requested but CUDA is unavailable")
    if requested == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("NFA_VIT_DEVICE=mps requested but PyTorch MPS is unavailable")
    return torch.device(requested)


def _install_registry_compatibility_shim() -> None:
    """Satisfy only the training registry decorator used by the upstream model."""
    try:
        from IMDLBenCo.registry import MODELS  # noqa: F401

        return
    except ImportError:
        pass

    class _ModelRegistry:
        def register_module(self):
            return lambda model_class: model_class

    package = types.ModuleType("IMDLBenCo")
    package.__path__ = []
    registry = types.ModuleType("IMDLBenCo.registry")
    registry.MODELS = _ModelRegistry()
    sys.modules.setdefault("IMDLBenCo", package)
    sys.modules.setdefault("IMDLBenCo.registry", registry)


def _load_upstream_model_class(model_dir: Path):
    package_name = "_content_trust_nfa_vit"
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(model_dir)]
        package.__package__ = package_name
        sys.modules[package_name] = package

    module_name = f"{package_name}.nfa_vit"
    module_path = model_dir / "nfa_vit.py"
    spec = spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load NFA-ViT model module from {module_path}")
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.NFA_ViT_modify1


def _download_model_weights() -> Path:
    download_args = {
        "repo_id": os.getenv("NFA_VIT_WEIGHTS_REPO", "Noihat/NFA-ViT"),
        "revision": os.getenv("NFA_VIT_WEIGHTS_REVISION", "cbc9383"),
        "allow_patterns": [
            "noiseprint.pth",
            "segformer_b0_backbone_weights.pth",
            "segformer_b2_backbone_weights.pth",
            "checkpoint-9999.pth",
        ],
    }
    try:
        cached_dir = Path(snapshot_download(**download_args, local_files_only=True))
    except LocalEntryNotFoundError:
        cached_dir = None
    if cached_dir is not None and all(
        (cached_dir / name).is_file() for name in download_args["allow_patterns"]
    ):
        return cached_dir

    try:
        return Path(snapshot_download(**download_args))
    except (httpx.TransportError, HfHubHTTPError) as network_error:
        raise RuntimeError(
            "Cannot reach Hugging Face and the complete NFA-ViT weights are not cached. "
            "Check the network proxy or download the model weights first."
        ) from network_error


@contextmanager
def _safe_torch_load_defaults(torch):
    """Use weights-only deserialization with the checkpoint's Namespace metadata."""
    original_load = torch.load

    def safe_load(*args, **kwargs):
        kwargs.setdefault("weights_only", True)
        return original_load(*args, **kwargs)

    with torch.serialization.safe_globals([argparse.Namespace]):
        torch.load = safe_load
        try:
            yield
        finally:
            torch.load = original_load
