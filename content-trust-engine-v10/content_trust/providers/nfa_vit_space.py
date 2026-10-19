"""Adapter for local or hosted NFA-ViT Gradio services.

NFA-ViT is used here only as a localized manipulation signal. Its score is not
reported as a probability that an image is fake, and it does not detect faces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from content_trust.schemas import ImageForensicsResult, LocalizationBox


class _NfaSpacePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    local_manipulation_score: float = Field(ge=0.0, le=1.0)
    localized_region_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    localized_region_bbox: dict[str, float] | None = None
    localization_score_method: str = "top_1_percent_mean_of_localization_mask"
    score_semantics: str = "uncalibrated localization-mask signal, not a probability"
    model_scope: str = "localized AI-generated/inpainted region detection"
    inference_device: str | None = None


class NfaVitSpaceClient:
    """Call a Gradio endpoint using the optional official Python client."""

    def __init__(
        self,
        space_url: str | None,
        *,
        token: str | None = None,
        timeout_seconds: float = 60.0,
        client_factory: Any | None = None,
    ) -> None:
        self._space_url = space_url
        self.provider_name = (
            "nfa_vit_local" if _is_loopback_url(space_url) else "nfa_vit_space"
        )
        self._token = token
        self._timeout = timeout_seconds
        self._client_factory = client_factory
        self._client: Any | None = None

    def analyze_image(
        self, *, image_path: str | None = None, image_url: str | None = None
    ) -> ImageForensicsResult:
        if not self._space_url:
            return self._unavailable("provider_not_configured")
        if (image_path is None) == (image_url is None):
            return self._unavailable("image_source_required")
        if image_path is not None and not Path(image_path).is_file():
            return self._unavailable("image_unavailable")

        try:
            last_error: Exception | None = None
            normalized: _NfaSpacePayload | None = None
            for attempt in range(2):
                try:
                    client = self._make_client()
                    source = image_path or image_url
                    if self._client_factory is None:
                        from gradio_client import handle_file

                        source = handle_file(source)
                    response = client.predict(source, api_name="/predict")
                    payload = response[0] if isinstance(response, (tuple, list)) else response
                    overlay_response = (
                        response[1]
                        if isinstance(response, (tuple, list)) and len(response) > 1
                        else None
                    )
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError as exc:
                            raise ValueError("invalid_json") from exc
                    normalized = _NfaSpacePayload.model_validate(payload)
                    if normalized.localized_region_bbox is not None:
                        LocalizationBox.model_validate(normalized.localized_region_bbox)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt == 1:
                        raise
            if normalized is None and last_error is not None:
                raise last_error
            if normalized is None:
                return self._unavailable("invalid_response")
            return ImageForensicsResult(
                provider=self.provider_name,
                localized_manipulation_score=normalized.local_manipulation_score,
                localized_region_fraction=normalized.localized_region_fraction,
                localized_region_bbox=(
                    LocalizationBox.model_validate(normalized.localized_region_bbox)
                    if normalized.localized_region_bbox is not None
                    else None
                ),
                localized_overlay_path=_local_image_path(overlay_response),
                localization_score_method=normalized.localization_score_method,
                score_semantics=normalized.score_semantics,
                inference_device=(
                    normalized.inference_device
                    if normalized.inference_device in {"cuda", "mps", "cpu"}
                    else None
                ),
                available=True,
                raw_response=normalized.model_dump(mode="json"),
            )
        except Exception as exc:
            return self._unavailable(_exception_code(exc))

    def _make_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory(
                self._space_url,
                token=self._token,
                timeout=self._timeout,
            )
            return self._client
        try:
            from gradio_client import Client
        except ImportError:
            raise RuntimeError("client_dependency_missing") from None
        self._client = Client(
            self._space_url,
            # gradio-client 1.x (the supported range in pyproject.toml) calls
            # this credential argument ``hf_token``. Passing ``token`` raises
            # TypeError before the request can reach the Gradio endpoint.
            hf_token=self._token,
            verbose=False,
            httpx_kwargs={"timeout": self._timeout},
        )
        return self._client

    def _unavailable(self, error: str) -> ImageForensicsResult:
        return ImageForensicsResult(
            provider=self.provider_name,
            available=False,
            error=error,
        )


def _exception_code(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException) or "timeout" in type(exc).__name__.lower():
        return "provider_timeout"
    if isinstance(exc, httpx.ConnectError) or any(
        marker in type(exc).__name__.lower() for marker in ("connect", "connection")
    ):
        return "provider_connection_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"provider_http_{exc.response.status_code}"
    if isinstance(exc, httpx.TransportError):
        return f"provider_network_error_{type(exc).__name__.lower()}"
    if isinstance(exc, ValidationError):
        return "invalid_response"
    if isinstance(exc, ValueError) and str(exc) == "invalid_json":
        return "invalid_json"
    if isinstance(exc, RuntimeError) and str(exc) == "client_dependency_missing":
        return "client_dependency_missing"
    return f"provider_error_{type(exc).__name__.lower()}"


def _is_loopback_url(url: str | None) -> bool:
    if not url:
        return False
    from urllib.parse import urlsplit

    return urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}


def _local_image_path(value: Any) -> str | None:
    """Return the downloaded Gradio image path when the response exposes one."""
    if isinstance(value, dict):
        value = value.get("path")
    elif not isinstance(value, (str, Path)):
        value = getattr(value, "path", None)
    if not isinstance(value, (str, Path)):
        return None
    path = Path(value)
    return str(path.resolve()) if path.is_file() else None
