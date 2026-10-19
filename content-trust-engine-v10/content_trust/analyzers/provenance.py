"""Extract optional provenance and EXIF metadata without turning absence into risk."""

from pathlib import Path
from typing import Any, Protocol

from content_trust.providers.c2pa import C2paPythonClient
from content_trust.schemas import ProvenanceAnalysis


class ProvenanceProvider(Protocol):
    def inspect(self, image_path: str) -> ProvenanceAnalysis: ...


class ProvenanceAnalyzer:
    def __init__(self, provider: ProvenanceProvider | None = None) -> None:
        self._provider = provider or C2paPythonClient()

    def analyze(
        self, *, image_path: str | None = None, image_url: str | None = None
    ) -> ProvenanceAnalysis:
        if image_path is None:
            return ProvenanceAnalysis(
                status="unavailable",
                error="local_file_required_for_provenance",
                exif_error="local_file_required_for_exif",
            )

        try:
            result = self._provider.inspect(image_path)
        except Exception as exc:
            result = ProvenanceAnalysis(
                status="unavailable",
                error=getattr(exc, "code", "c2pa_provider_error"),
            )

        exif_values, exif_error = _read_exif(image_path)
        return result.model_copy(
            update={
                "exif_available": exif_error is None,
                "exif_present": bool(exif_values) if exif_error is None else None,
                "exif_camera_make": exif_values.get("make"),
                "exif_camera_model": exif_values.get("model"),
                "exif_datetime_original": exif_values.get("datetime_original"),
                "exif_software": exif_values.get("software"),
                "exif_error": exif_error,
            }
        )


def _read_exif(image_path: str) -> tuple[dict[str, str], str | None]:
    path = Path(image_path)
    if not path.is_file():
        return {}, "image_unavailable"
    try:
        from PIL import ExifTags, Image
    except ImportError:
        return {}, "pillow_dependency_missing"

    try:
        with Image.open(path) as image:
            exif = image.getexif()
        tags = {ExifTags.TAGS.get(key, str(key)): value for key, value in exif.items()}
        exif_ifd_id = getattr(ExifTags, "IFD", None)
        exif_ifd = tags.get("Exif\x00", {})
        if exif_ifd_id is not None:
            try:
                with Image.open(path) as image:
                    exif_ifd = image.getexif().get_ifd(exif_ifd_id.Exif)
                exif_ifd = {
                    ExifTags.TAGS.get(key, str(key)): value
                    for key, value in exif_ifd.items()
                }
            except Exception:
                exif_ifd = {}
        values = {
            "make": _string(tags.get("Make")),
            "model": _string(tags.get("Model")),
            "datetime_original": _string(exif_ifd.get("DateTimeOriginal")),
            "software": _string(tags.get("Software")),
        }
        return {key: value for key, value in values.items() if value}, None
    except Exception:
        return {}, "exif_read_failed"


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
