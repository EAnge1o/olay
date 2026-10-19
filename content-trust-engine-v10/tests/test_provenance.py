import pytest
import sys
from types import SimpleNamespace

from content_trust.analyzers.provenance import ProvenanceAnalyzer
from content_trust.fusion.assessment import review_priority
from content_trust.providers.c2pa import _normalized_status
from content_trust.providers.c2pa import C2paPythonClient
from content_trust.schemas import ProvenanceAnalysis


def test_missing_manifest_and_exif_are_neutral(tmp_path) -> None:
    pil = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "ordinary.png"
    pil.new("RGB", (8, 8), color=(30, 100, 200)).save(image_path)

    class NoManifestProvider:
        def inspect(self, path):
            return ProvenanceAnalysis(
                status="absent",
                manifest_present=False,
                validation_state="no manifest",
            )

    result = ProvenanceAnalyzer(NoManifestProvider()).analyze(
        image_path=str(image_path)
    )

    assert result.status == "absent"
    assert result.exif_present is False
    assert review_priority(findings=[], provenance_analysis=result) == "low"


def test_c2pa_validation_enum_strings_normalize_without_guessing() -> None:
    assert _normalized_status("ValidationState.VALID") == "valid"
    assert _normalized_status("ValidationState.INVALID") == "invalid"
    assert _normalized_status("unknown") == "unavailable"


def test_c2pa_manifest_not_found_is_absent_not_provider_error(
    tmp_path, monkeypatch
) -> None:
    image_path = tmp_path / "ordinary.jpg"
    image_path.write_bytes(b"image bytes")

    class FakeC2paError(Exception):
        @property
        def message(self):
            return str(self)

    class FakeContext:
        @classmethod
        def from_dict(cls, _config):
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class FakeReader:
        def __init__(self, *_args, **_kwargs):
            raise FakeC2paError("ManifestNotFound: no JUMBF data found")

    monkeypatch.setitem(
        sys.modules,
        "c2pa",
        SimpleNamespace(Context=FakeContext, Reader=FakeReader, C2paError=FakeC2paError),
    )
    result = C2paPythonClient().inspect(str(image_path))

    assert result.status == "absent"
    assert result.manifest_present is False
    assert result.error is None
