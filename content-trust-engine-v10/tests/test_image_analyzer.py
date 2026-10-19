import httpx

from content_trust.analyzers.image import ImageAnalyzer
from content_trust.config import Settings
from content_trust.providers.nfa_vit_space import NfaVitSpaceClient
from content_trust.schemas import ImageForensicsResult
from content_trust.schemas import AIGenerationSignal, TruForSignal


class FakeSpaceClient:
    def __init__(self, response=None, error=None, overlay="localized-mask.png"):
        self.response = response
        self.error = error
        self.overlay = overlay
        self.calls = []

    def predict(self, image, *, api_name):
        self.calls.append((image, api_name))
        if self.error:
            raise self.error
        return [self.response, self.overlay]


def test_nfa_vit_normalizes_local_signal_and_keeps_raw_internal() -> None:
    client = FakeSpaceClient(
        {
            "provider": "NFA-ViT",
            "local_manipulation_score": 0.94,
            "localized_region_fraction": 0.12,
            "localized_region_bbox": {
                "x_min": 0.1,
                "y_min": 0.2,
                "x_max": 0.5,
                "y_max": 0.6,
            },
            "model_scope": "localized AI-generated/inpainted region detection",
        }
    )
    provider = NfaVitSpaceClient(
        "https://huggingface.co/spaces/owner/private-space",
        token="hf-test",
        client_factory=lambda *args, **kwargs: client,
    )

    result = provider.analyze_image(image_url="https://example.test/image.jpg")

    assert result.available is True
    assert result.localized_manipulation_score == 0.94
    assert result.localized_region_fraction == 0.12
    assert result.localized_region_bbox.x_min == 0.1
    assert result.raw_response["model_scope"].startswith("localized")
    assert client.calls[0][1] == "/predict"


def test_nfa_vit_exposes_downloaded_localization_overlay_path(tmp_path) -> None:
    overlay = tmp_path / "localized-mask.png"
    overlay.write_bytes(b"overlay")
    client = FakeSpaceClient(
        {
            "local_manipulation_score": 0.94,
            "localized_region_fraction": 0.12,
        },
        overlay=str(overlay),
    )
    provider = NfaVitSpaceClient(
        "http://127.0.0.1:7860",
        client_factory=lambda *args, **kwargs: client,
    )

    result = provider.analyze_image(image_url="https://example.test/image.jpg")

    assert result.localized_overlay_path == str(overlay.resolve())


def test_nfa_vit_missing_space_degrades_without_calling_provider() -> None:
    provider = NfaVitSpaceClient(None)

    result = provider.analyze_image(image_url="https://example.test/image.jpg")

    assert result.available is False
    assert result.error == "provider_not_configured"


def test_nfa_vit_timeout_retries_once_then_returns_partial_failure() -> None:
    client = FakeSpaceClient(error=httpx.ReadTimeout("timeout"))
    provider = NfaVitSpaceClient(
        "https://huggingface.co/spaces/owner/private-space",
        client_factory=lambda *args, **kwargs: client,
    )

    result = provider.analyze_image(image_url="https://example.test/image.jpg")

    assert result.available is False
    assert result.error == "provider_timeout"
    assert len(client.calls) == 2


def test_image_analyzer_finds_local_manipulation_only() -> None:
    class FakeImageProvider:
        provider_name = "nfa_vit_space"

        def analyze_image(self, **kwargs):
            return ImageForensicsResult(
                provider=self.provider_name,
                available=True,
                localized_manipulation_score=0.94,
                localized_region_fraction=0.12,
            )

    analyzer = ImageAnalyzer(FakeImageProvider(), settings=Settings())

    result, findings = analyzer.analyze(image_url="https://example.test/image.jpg")

    assert result.available is True
    assert [finding.category for finding in findings] == [
        "localized_image_manipulation"
    ]
    assert findings[0].severity == "info"
    assert findings[0].verdict == "unknown"
    assert findings[0].confidence is None


def test_lota_nfa_and_trufor_run_independently_without_threshold_gate() -> None:
    class FakeNfa:
        provider_name = "nfa_vit_space"

        def __init__(self):
            self.calls = 0

        def analyze_image(self, **kwargs):
            self.calls += 1
            return ImageForensicsResult(
                provider=self.provider_name,
                available=True,
                localized_manipulation_score=0.12,
            )

    class FakeLota:
        provider_name = "lota_local"

        def __init__(self):
            self.calls = 0

        def analyze_image(self, **kwargs):
            self.calls += 1
            return AIGenerationSignal(
                provider=self.provider_name,
                available=True,
                score=0.01,
            )

    class FakeTruFor:
        provider_name = "trufor_local"

        def __init__(self):
            self.calls = 0

        def analyze_image(self, **kwargs):
            self.calls += 1
            return TruForSignal(
                available=True,
                detection_score=0.22,
                localization_score=0.31,
                reliability_mean=0.8,
            )

    nfa, lota, trufor = FakeNfa(), FakeLota(), FakeTruFor()
    analyzer = ImageAnalyzer(
        nfa,
        generation_provider=lota,
        trufor_provider=trufor,
    )

    result, findings = analyzer.analyze(image_url="https://example.test/image.jpg")

    assert (lota.calls, nfa.calls, trufor.calls) == (1, 1, 1)
    assert result.ai_generation_signal.score == 0.01
    assert result.localized_manipulation_score == 0.12
    assert result.trufor_analysis.detection_score == 0.22
    assert all(finding.verdict == "unknown" for finding in findings)
