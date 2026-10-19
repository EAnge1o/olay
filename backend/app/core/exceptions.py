"""Domain exceptions.

Services raise these; a single handler in ``main`` converts them into a
consistent JSON error envelope:

    {"error": {"code": "...", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected, client-facing application errors."""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    """Requested state transition is invalid (e.g. task already running)."""

    status_code = 409
    code = "conflict"


class UnsupportedMediaError(AppError):
    status_code = 415
    code = "unsupported_media_type"


class ExternalServiceError(AppError):
    """An upstream dependency (LLM, vision model) failed."""

    status_code = 502
    code = "external_service_error"
