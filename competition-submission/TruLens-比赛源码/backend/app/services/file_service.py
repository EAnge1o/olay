"""File ingest: validation, streamed persistence, and metadata probing.

Kept deliberately storage-agnostic in spirit: today it writes to a local
directory, but the same surface would move to S3/OSS by swapping ``_open`` and
the path bookkeeping.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import UnsupportedMediaError, ValidationError
from app.core.utils import new_id

logger = logging.getLogger(__name__)

_READ_CHUNK = 256 * 1024

# Suffix -> MIME, for images that arrive as files on disk rather than as a
# multipart upload (the XHS crawler saves .webp).
_MIME_BY_SUFFIX: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class FileService:
    def __init__(
        self,
        storage_dir: Path,
        allowed_types: list[str],
        max_bytes: int,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.allowed_types = set(allowed_types or [])
        self.max_bytes = max_bytes
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls) -> "FileService":
        s = get_settings()
        return cls(s.storage_dir, s.allowed_image_types, s.max_upload_size_bytes)

    async def save_image(self, upload: UploadFile) -> dict:
        """Persist an uploaded image and return its metadata record."""
        if upload is None or not upload.filename:
            raise ValidationError("image file is required")

        content_type = (upload.content_type or "").lower()
        if self.allowed_types and content_type not in self.allowed_types:
            raise UnsupportedMediaError(
                f"unsupported image type: {content_type or 'unknown'}",
                details={"allowed": sorted(self.allowed_types)},
            )

        suffix = Path(upload.filename).suffix.lower() or ".bin"
        stored_name = f"{uuid4().hex}{suffix}"
        dest = self.storage_dir / stored_name
        digest = hashlib.sha256()
        size = 0

        try:
            with dest.open("wb") as handle:
                while True:
                    chunk = await upload.read(_READ_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UnsupportedMediaError(
                            f"image exceeds {self.max_bytes // (1024 * 1024)}MB limit"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        except Exception:
            dest.unlink(missing_ok=True)  # never leave partial files behind
            raise

        meta = {
            "stored_name": stored_name,
            "filename": upload.filename,
            "content_type": content_type,
            "size": size,
            "sha256": digest.hexdigest(),
            "probe_id": new_id(),
        }
        meta.update(self._probe(dest))
        return {"path": str(dest), "filename": upload.filename, "meta": meta}

    def save_local_image(
        self,
        source: Path,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> dict:
        """Persist an image that already exists on disk (e.g. an XHS download).

        Same validation and metadata as :meth:`save_image`, minus the multipart
        stream — the caller hands us a path instead of an ``UploadFile``.
        """
        source = Path(source)
        if not source.is_file():
            raise ValidationError(f"image file not found: {source}")

        suffix = source.suffix.lower() or ".bin"
        guessed_type = content_type or _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
        if self.allowed_types and guessed_type not in self.allowed_types:
            raise UnsupportedMediaError(
                f"unsupported image type: {guessed_type or 'unknown'}",
                details={"allowed": sorted(self.allowed_types), "source": str(source)},
            )

        stored_name = f"{uuid4().hex}{suffix}"
        dest = self.storage_dir / stored_name
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as reader, dest.open("wb") as writer:
                while True:
                    chunk = reader.read(_READ_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UnsupportedMediaError(
                            f"image exceeds {self.max_bytes // (1024 * 1024)}MB limit"
                        )
                    digest.update(chunk)
                    writer.write(chunk)
        except Exception:
            dest.unlink(missing_ok=True)
            raise

        meta = {
            "stored_name": stored_name,
            "filename": filename or source.name,
            "content_type": guessed_type,
            "size": size,
            "sha256": digest.hexdigest(),
            "probe_id": new_id(),
            "origin": "local_file",
        }
        meta.update(self._probe(dest))
        return {"path": str(dest), "filename": filename or source.name, "meta": meta}

    @staticmethod
    def _probe(path: Path) -> dict:
        """Best-effort image introspection. Degrades gracefully without Pillow."""
        try:
            from PIL import Image  # optional dependency
        except ImportError:
            return {"probe": "unavailable"}

        try:
            with Image.open(path) as image:
                width, height = image.size
                exif = {}
                try:
                    exif = dict(image.getexif())
                except Exception:  # noqa: BLE001 - malformed EXIF should not fail ingest
                    exif = {}
                return {
                    "probe": "pillow",
                    "width": width,
                    "height": height,
                    "format": image.format,
                    "mode": image.mode,
                    "has_exif": bool(exif),
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("image probe failed for %s: %s", path.name, exc)
            return {"probe": "failed", "error": str(exc)}
