"""Run optional legacy image-forensics providers independently."""

from typing import Any

from content_trust.config import Settings
from content_trust.schemas import (
    AIGenerationSignal,
    Finding,
    ImageForensicsResult,
    TruForSignal,
)


class ImageAnalyzer:
    def __init__(
        self,
        provider: Any | None = None,
        *,
        generation_provider: Any | None = None,
        trufor_provider: Any | None = None,
        settings: Settings | None = None,
        enable_legacy_defaults: bool = True,
    ) -> None:
        self._settings = settings or Settings()
        provider_was_supplied = provider is not None
        if provider is None and enable_legacy_defaults:
            from content_trust.providers.nfa_vit_space import NfaVitSpaceClient

            provider = NfaVitSpaceClient(
                self._settings.nfa_vit_space_url,
                token=self._settings.huggingface_token,
                timeout_seconds=self._settings.timeout_seconds,
            )
        self._provider = provider
        if (
            generation_provider is None
            and not provider_was_supplied
            and enable_legacy_defaults
        ):
            from content_trust.providers.lota import LotaLocalClient

            generation_provider = LotaLocalClient(
                self._settings.lota_weights_path,
                device=self._settings.lota_device,
                timeout_seconds=self._settings.timeout_seconds,
            )
        self._generation_provider = generation_provider
        self._trufor_provider = trufor_provider

    def analyze(
        self, *, image_path: str | None = None, image_url: str | None = None
    ) -> tuple[ImageForensicsResult, list[Finding]]:
        generation = self._analyze_generation(image_path=image_path, image_url=image_url)
        nfa = self._analyze_nfa(image_path=image_path, image_url=image_url)
        trufor = self._analyze_trufor(image_path=image_path, image_url=image_url)

        errors = [
            f"{name}:{error}"
            for name, error in (
                ("lota", generation.error),
                ("nfa_vit", nfa.error if nfa is not None else None),
                ("trufor", trufor.error if trufor is not None else None),
            )
            if error and error != "provider_not_configured"
        ]
        available = bool(
            generation.available
            or (nfa is not None and nfa.available)
            or (trufor is not None and trufor.available)
        )
        nfa_status = (
            "not_configured"
            if nfa is None or nfa.error == "provider_not_configured"
            else "available"
            if nfa.available
            else "unavailable"
        )
        provider_names = [
            name
            for name, result in (
                (generation.provider, generation.available),
                (nfa.provider if nfa is not None else None, nfa.available if nfa else False),
                (trufor.provider if trufor is not None else None, trufor.available if trufor else False),
            )
            if name and result
        ]

        result = ImageForensicsResult(
            provider="+".join(dict.fromkeys(provider_names)) or "legacy_forensics_disabled",
            ai_generation_signal=generation,
            nfa_status=nfa_status,
            nfa_error=(nfa.error if nfa is not None else None),
            localized_manipulation_score=(
                nfa.localized_manipulation_score if nfa is not None else None
            ),
            localized_region_fraction=(
                nfa.localized_region_fraction if nfa is not None else None
            ),
            localized_region_bbox=(
                nfa.localized_region_bbox if nfa is not None else None
            ),
            localized_overlay_path=(
                nfa.localized_overlay_path if nfa is not None else None
            ),
            localization_score_method=(
                nfa.localization_score_method if nfa is not None else None
            ),
            trufor_analysis=trufor,
            score_semantics=nfa.score_semantics if nfa is not None else None,
            inference_device=(
                generation.inference_device
                or (trufor.inference_device if trufor is not None else None)
                or (nfa.inference_device if nfa is not None else None)
            ),
            available=available,
            error=None if available else _first_error(generation, nfa, trufor),
            provider_errors=errors,
            raw_response={
                "nfa_vit": nfa.raw_response if nfa is not None else {},
            },
        )
        findings = _signal_findings(result)
        return result, findings

    def _analyze_generation(
        self, *, image_path: str | None, image_url: str | None
    ) -> AIGenerationSignal:
        if self._generation_provider is None:
            return AIGenerationSignal(
                provider="legacy_forensics_disabled",
                available=False,
                error="provider_not_configured",
            )
        try:
            return self._generation_provider.analyze_image(
                image_path=image_path,
                image_url=image_url,
            )
        except Exception as exc:
            return AIGenerationSignal(
                provider=getattr(self._generation_provider, "provider_name", "lota"),
                available=False,
                error=_error_code(exc, "lota"),
            )

    def _analyze_nfa(
        self, *, image_path: str | None, image_url: str | None
    ) -> ImageForensicsResult | None:
        if self._provider is None:
            return None
        try:
            return self._provider.analyze_image(
                image_path=image_path,
                image_url=image_url,
            )
        except Exception as exc:
            return ImageForensicsResult(
                provider=getattr(self._provider, "provider_name", "nfa_vit"),
                available=False,
                error=_error_code(exc, "nfa_vit"),
            )

    def _analyze_trufor(
        self, *, image_path: str | None, image_url: str | None
    ) -> TruForSignal | None:
        if self._trufor_provider is None:
            return None
        try:
            return self._trufor_provider.analyze_image(
                image_path=image_path,
                image_url=image_url,
            )
        except Exception as exc:
            return TruForSignal(
                provider=getattr(self._trufor_provider, "provider_name", "trufor_local"),
                available=False,
                error=_error_code(exc, "trufor"),
            )


def _error_code(exc: Exception, provider: str) -> str:
    error = getattr(exc, "code", None)
    if not error or error == "provider_error":
        error = f"{provider}_error_{type(exc).__name__.lower()}"
    return error


def _first_error(generation, nfa, trufor) -> str | None:
    return next(
        (
            error
            for error in (
                generation.error,
                nfa.error if nfa is not None else None,
                trufor.error if trufor is not None else None,
            )
            if error
            and error != "provider_not_configured"
        ),
        None,
    )


def _signal_findings(result: ImageForensicsResult) -> list[Finding]:
    findings: list[Finding] = []
    generation = result.ai_generation_signal
    if generation is not None and generation.available and generation.score is not None:
        findings.append(
            Finding(
                id="lota-global-generation-signal",
                category="ai_generated_image",
                verdict="unknown",
                severity="info",
                risk_score=None,
                signal_score=generation.score,
                score_semantics=generation.score_semantics,
                confidence=None,
                observation=(
                    f"LOTA experimental whole-image signal: {generation.score:.3f}."
                ),
                reason=(
                    "This is an uncalibrated global-generation detector output. "
                    "It is not an AI-generation probability or proof of deception."
                ),
                source="image_forensics",
                recommended_action=(
                    "Compare with source history and beauty-domain calibration results; "
                    "do not make an authenticity decision from this score alone."
                ),
            )
        )

    if (
        result.localized_manipulation_score is not None
        or result.localized_overlay_path
    ):
        findings.append(
            Finding(
                id="nfa-localization-signal",
                category="localized_image_manipulation",
                verdict="unknown",
                severity="info",
                risk_score=None,
                signal_score=result.localized_manipulation_score,
                score_semantics=(
                    result.score_semantics
                    or "uncalibrated NFA-ViT localization-mask signal; not a probability"
                ),
                observation=(
                    "NFA-ViT returned a localized candidate region; the overlay and "
                    "bounding box are model localization outputs."
                ),
                reason=(
                    "A highlighted region is not proof of editing and does not "
                    "explain a whole-image generation score."
                ),
                source="image_forensics",
                recommended_action=(
                    "Inspect the candidate region alongside the original file and "
                    "independent capture or edit history."
                ),
            )
        )

    trufor = result.trufor_analysis
    if trufor is not None and trufor.available:
        findings.append(
            Finding(
                id="trufor-local-manipulation-signal",
                category="localized_image_manipulation",
                verdict="unknown",
                severity="info",
                risk_score=None,
                signal_score=trufor.detection_score,
                score_semantics=trufor.score_semantics,
                observation=(
                    "TruFor returned an image integrity signal and a pixel-level "
                    "localization map. Its mean reliability-map value is "
                    f"{trufor.reliability_mean!s}."
                ),
                reason=(
                    "TruFor is a manipulation-detection signal; its map and reliability "
                    "output are not a calibrated probability or proof of intentional deception."
                ),
                source="image_forensics",
                recommended_action=(
                    "Review the localization map and reliability signal against the "
                    "original image and its source history."
                ),
            )
        )
    return findings
