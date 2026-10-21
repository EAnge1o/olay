"""Optional local adapter for the official LOTA ResNet-50 checkpoint.

The checkpoint's sigmoid output is the real-image score (natural images are
labelled 1 and AI images 0 in the upstream loader). This adapter reports its
complement as an uncalibrated AI-generation score.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from content_trust.schemas import AIGenerationSignal


class LotaLocalClient:
    provider_name = "lota_local"

    def __init__(
        self,
        weights_path: str = "models/LOTA/Network_best.pth",
        *,
        device: str = "auto",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._weights_path = _resolve_weights_path(weights_path)
        self._requested_device = device
        self._timeout = timeout_seconds
        self._model: Any | None = None
        self._torch: Any | None = None
        self._device: str | None = None

    def analyze_image(
        self,
        *,
        image_path: str | None = None,
        image_url: str | None = None,
        preprocessing_seed: int = 42,
    ) -> AIGenerationSignal:
        if (image_path is None) == (image_url is None):
            return self._unavailable("image_source_required")
        if not self._weights_path.is_file():
            return self._unavailable("checkpoint_not_found")

        try:
            image = self._load_image(image_path=image_path, image_url=image_url)
            model, torch, device = self._load_model()
            tensor = _preprocess(
                image, torch, seed=preprocessing_seed
            ).unsqueeze(0).to(device)
            try:
                with torch.inference_mode():
                    # Compute the complementary sigmoid directly. ``1 - sigmoid(x)``
                    # can round to exactly zero for large positive real-class logits.
                    ai_score = float(torch.sigmoid(-model(tensor)).reshape(-1)[0].item())
            except Exception:
                if device != "mps" or self._requested_device != "auto":
                    raise
                device = "cpu"
                model.to(device)
                self._device = device
                tensor = tensor.cpu()
                with torch.inference_mode():
                    ai_score = float(torch.sigmoid(-model(tensor)).reshape(-1)[0].item())
            return AIGenerationSignal(
                provider=self.provider_name,
                score=max(0.0, min(1.0, ai_score)),
                available=True,
                score_semantics=(
                    "sigmoid(-real-class logit); uncalibrated LOTA AI-generation "
                    "score, not a probability"
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

        device = _select_device(torch, self._requested_device)
        model = _build_lota_model(torch)
        checkpoint = torch.load(
            self._weights_path,
            map_location="cpu",
            weights_only=True,
        )
        if isinstance(checkpoint, dict):
            for key in ("state_dict", "model_state_dict", "model"):
                candidate = checkpoint.get(key)
                if isinstance(candidate, dict):
                    checkpoint = candidate
                    break
        if not isinstance(checkpoint, dict):
            raise RuntimeError("invalid_checkpoint")
        state = {
            key.removeprefix("module."): value
            for key, value in checkpoint.items()
        }
        try:
            model.load_state_dict(state, strict=True)
        except (RuntimeError, TypeError, ValueError):
            raise RuntimeError("invalid_checkpoint") from None
        model.to(device)
        model.eval()
        self._model, self._torch, self._device = model, torch, device
        return model, torch, device

    def _load_image(self, *, image_path: str | None, image_url: str | None) -> Any:
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


def _resolve_weights_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    project_root = Path(__file__).resolve().parents[2]
    return project_root / candidate


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


def _build_lota_model(torch: Any) -> Any:
    """Build the upstream ResNet-50 state-dict layout without torchvision."""
    nn = torch.nn

    class Bottleneck(nn.Module):
        expansion = 4

        def __init__(self, inplanes: int, planes: int, stride: int = 1, downsample: Any = None):
            super().__init__()
            self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, bias=False)
            self.bn1 = nn.BatchNorm2d(planes)
            self.conv2 = nn.Conv2d(
                planes, planes, kernel_size=3, stride=stride, padding=1, bias=False
            )
            self.bn2 = nn.BatchNorm2d(planes)
            self.conv3 = nn.Conv2d(
                planes, planes * self.expansion, kernel_size=1, bias=False
            )
            self.bn3 = nn.BatchNorm2d(planes * self.expansion)
            self.relu = nn.ReLU(inplace=True)
            self.downsample = downsample

        def forward(self, inputs: Any) -> Any:
            identity = inputs
            output = self.relu(self.bn1(self.conv1(inputs)))
            output = self.relu(self.bn2(self.conv2(output)))
            output = self.bn3(self.conv3(output))
            if self.downsample is not None:
                identity = self.downsample(inputs)
            return self.relu(output + identity)

    class ResNet50(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.inplanes = 64
            self.conv1 = nn.Conv2d(
                3, 64, kernel_size=7, stride=2, padding=3, bias=False
            )
            self.bn1 = nn.BatchNorm2d(64)
            self.relu = nn.ReLU(inplace=True)
            self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
            self.layer1 = self._make_layer(64, 3)
            self.layer2 = self._make_layer(128, 4, stride=2)
            self.layer3 = self._make_layer(256, 6, stride=2)
            self.layer4 = self._make_layer(512, 3, stride=2)
            self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
            self.fc = nn.Linear(2048, 1)

        def _make_layer(self, planes: int, blocks: int, stride: int = 1) -> Any:
            downsample = None
            if stride != 1 or self.inplanes != planes * Bottleneck.expansion:
                downsample = nn.Sequential(
                    nn.Conv2d(
                        self.inplanes,
                        planes * Bottleneck.expansion,
                        kernel_size=1,
                        stride=stride,
                        bias=False,
                    ),
                    nn.BatchNorm2d(planes * Bottleneck.expansion),
                )
            layers = [Bottleneck(self.inplanes, planes, stride, downsample)]
            self.inplanes = planes * Bottleneck.expansion
            layers.extend(Bottleneck(self.inplanes, planes) for _ in range(1, blocks))
            return nn.Sequential(*layers)

        def forward(self, inputs: Any) -> Any:
            output = self.maxpool(self.relu(self.bn1(self.conv1(inputs))))
            output = self.layer1(output)
            output = self.layer2(output)
            output = self.layer3(output)
            output = self.layer4(output)
            output = self.avgpool(output).flatten(1)
            return self.fc(output)

    class LotaClassifier(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.disc = ResNet50()

        def forward(self, inputs: Any) -> Any:
            return self.disc(inputs)

    return LotaClassifier()


def _preprocess(image: Any, torch: Any, *, seed: int = 42) -> Any:
    tensor, _ = _preprocess_with_trace(image, torch, seed=seed)
    return tensor


def _preprocess_with_trace(
    image: Any, torch: Any, *, seed: int = 42
) -> tuple[Any, dict[str, Any]]:
    """Run the production preprocessing and return a diagnostic trace."""
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        raise RuntimeError("dependency_missing") from None

    pixels = np.asarray(image.convert("RGB"), dtype=np.uint8)
    low_bits = ((pixels & 0x07) * (255 // 7)).astype(np.uint8)
    bit_image = Image.fromarray(low_bits)
    width, height = bit_image.size
    original_size = [width, height]
    patch_size = 32
    image_size = 256
    pre_resize_applied = min(width, height) < patch_size
    if min(width, height) < patch_size:
        bit_image = bit_image.resize(
            (image_size, image_size), resample=Image.Resampling.BILINEAR
        )
        width, height = bit_image.size

    array = np.asarray(bit_image, dtype=np.uint8)
    # Keep patch sampling deterministic. Experiments can pass the same seed to
    # paired originals/variants to isolate image transformations from crop noise.
    generator = torch.Generator(device="cpu").manual_seed(int(seed) % (2**63 - 1))
    candidates: list[tuple[int, int, int, Any]] = []
    for index in range((image_size // patch_size) ** 2):
        top = int(torch.randint(0, height - patch_size + 1, (1,), generator=generator))
        left = int(torch.randint(0, width - patch_size + 1, (1,), generator=generator))
        patch = array[top : top + patch_size, left : left + patch_size]
        candidates.append((index, top, left, patch))
    scored_candidates = [
        (index, top, left, _gradient_score(patch, np), patch)
        for index, top, left, patch in candidates
    ]
    selected = max(scored_candidates, key=lambda item: item[3])
    resized = cv2.resize(
        selected[4],
        (image_size, image_size),
        interpolation=cv2.INTER_LINEAR,
    )
    tensor = torch.from_numpy(resized.astype(np.float32) / 255.0)
    tensor = tensor.permute(2, 0, 1)
    mean_values = (0.485, 0.456, 0.406)
    std_values = (0.229, 0.224, 0.225)
    mean = torch.tensor(mean_values, dtype=tensor.dtype).view(3, 1, 1)
    std = torch.tensor(std_values, dtype=tensor.dtype).view(3, 1, 1)
    tensor = (tensor - mean) / std

    def array_hash(value: Any) -> str:
        return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()

    tensor_array = tensor.detach().cpu().numpy()
    trace = {
        "input_rgb": {
            "shape_hwc": list(pixels.shape),
            "channel_order": "RGB",
            "pixel_sha256": array_hash(pixels),
        },
        "bit_plane": {
            "operation": "(channel & 0x07) * (255 // 7)",
            "shape_hwc": list(low_bits.shape),
            "range": [int(low_bits.min()), int(low_bits.max())],
            "pixel_sha256": array_hash(low_bits),
        },
        "patch_selection": {
            "rng": "torch.Generator(device='cpu')",
            "seed": int(seed),
            "candidate_count": len(scored_candidates),
            "patch_size": patch_size,
            "pre_resize_applied": pre_resize_applied,
            "pre_resize_from": original_size if pre_resize_applied else None,
            "pre_resize_to": [image_size, image_size] if pre_resize_applied else None,
            "candidates": [
                {
                    "index": index,
                    "top": top,
                    "left": left,
                    "gradient_score": score,
                }
                for index, top, left, score, _ in scored_candidates
            ],
            "selected_index": selected[0],
            "selected_bbox_xyxy": [
                selected[2], selected[1], selected[2] + patch_size, selected[1] + patch_size
            ],
            "selected_patch_sha256": array_hash(selected[4]),
        },
        "final_resize": {
            "implementation": "opencv.resize",
            "interpolation": "INTER_LINEAR",
            "from": [patch_size, patch_size],
            "to": [image_size, image_size],
            "rgb_sha256": array_hash(resized),
        },
        "normalization": {
            "input_scale": "uint8 / 255",
            "mean": list(mean_values),
            "std": list(std_values),
            "tensor_shape_chw": list(tensor_array.shape),
            "tensor_range": [float(tensor_array.min()), float(tensor_array.max())],
            "tensor_sha256": array_hash(tensor_array),
        },
    }
    return tensor, trace


def _gradient_score(patch: Any, np: Any) -> int:
    pixels = patch.astype(np.int64)
    horizontal = np.abs(pixels[:, :-1, :] - pixels[:, 1:, :]).sum()
    vertical = np.abs(pixels[:-1, :, :] - pixels[1:, :, :]).sum()
    diagonal = np.abs(pixels[:-1, :-1, :] - pixels[1:, 1:, :]).sum()
    diagonal += np.abs(pixels[1:, :-1, :] - pixels[:-1, 1:, :]).sum()
    return int(horizontal + vertical + diagonal)


def _error_code(exc: Exception) -> str:
    if str(exc) in {
        "checkpoint_not_found",
        "dependency_missing",
        "device_unavailable",
        "image_source_required",
        "image_unavailable",
        "invalid_checkpoint",
        "invalid_device",
    }:
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "provider_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"provider_http_{exc.response.status_code}"
    if isinstance(exc, httpx.TransportError):
        return "provider_network_error"
    return f"provider_error_{type(exc).__name__.lower()}"
