"""Shared FastAPI dependencies.

Long-lived collaborators (file / analysis / report services) are built once in
the app lifespan and stashed on ``app.state``; these accessors just hand them
to routes. This keeps routes thin and makes services trivially swappable in
tests by overriding ``app.state``.
"""

from __future__ import annotations

from fastapi import Request

from app.database.session import get_session
from app.services.analysis_service import AnalysisService
from app.services.file_service import FileService
from app.services.report_service import ReportService
from app.services.xhs_service import XhsService

# Re-exported so routers import the DB dependency from one place.
get_db = get_session


def get_file_service(request: Request) -> FileService:
    return request.app.state.file_service


def get_xhs_service(request: Request) -> XhsService:
    return request.app.state.xhs_service


def get_analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


def get_report_service(request: Request) -> ReportService:
    return request.app.state.report_service
