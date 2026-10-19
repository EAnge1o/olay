"""FastAPI application factory and process entrypoint.

Run locally:
    uvicorn app.main:app --reload
or:
    python run.py
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.agents.registry import build_default_registry
from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.database.init_db import dispose_db, init_db
from app.database.session import SessionLocal
from app.services.analysis_service import AnalysisService
from app.services.file_service import FileService
from app.services.llm import get_llm_provider
from app.services.report_service import ReportService
from app.services.trust_engine_runner import TrustEngineRunner
from app.services.xhs_service import XhsService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the process-wide singletons once, tear them down cleanly."""
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")
    settings.ensure_directories()

    await init_db()

    # Concrete model is chosen here and nowhere else. Agents only ever see the
    # LLMProvider interface.
    llm = get_llm_provider(settings) if settings.enable_llm else None
    registry = build_default_registry(llm=llm)

    # Applied-AI engine (optional, default off). When enabled and importable it
    # produces the analysis dimensions; the heuristics become the fallback.
    engine_runner: TrustEngineRunner | None = None
    if settings.trust_engine_enabled:
        candidate = TrustEngineRunner.from_settings(settings)
        engine_runner = candidate if candidate.available else None
        if engine_runner is None:
            logger.warning(
                "TRUST_ENGINE_ENABLED=true but the engine is unusable; "
                "the built-in heuristics will be used"
            )

    app.state.trust_engine_runner = engine_runner
    app.state.settings = settings
    file_service = FileService.from_settings()
    # Link ingestion (paste a Xiaohongshu URL). Optional: a disabled or missing
    # crawler degrades to a clear error, never to guessed content.
    xhs_service = XhsService.from_settings(settings)
    app.state.file_service = file_service
    app.state.xhs_service = xhs_service
    app.state.analysis_service = AnalysisService(
        session_factory=SessionLocal,
        registry=registry,
        default_use_llm=settings.enable_llm,
        engine_runner=engine_runner,
        file_service=file_service,
        xhs_service=xhs_service,
    )
    app.state.report_service = ReportService()

    logger.info(
        "%s v%s started (env=%s, llm=%s, analysis_backend=%s, xhs=%s)",
        settings.app_name,
        __version__,
        settings.environment,
        "enabled" if settings.enable_llm else "heuristics-only",
        "trust_engine" if engine_runner is not None else "heuristics",
        "ready" if xhs_service.available else "off",
    )
    try:
        yield
    finally:
        await dispose_db()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "TruLens · 美妆内容可信度核验 Agent 平台。\n\n"
            "上传种草文案 / 图片 / 评论 / 账号历史 → 四个维度并行分析 → 输出结构化可信度报告"
            "（T 账号可信度 / R 图片真实性 / U 评论区真实性 / S 文案真实性）。"
        ),
        lifespan=lifespan,
        debug=settings.debug,
    )

    origins = settings.cors_origins or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", tags=["system"], summary="服务信息")
    async def root() -> dict:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "api_prefix": settings.api_prefix,
        }

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """One consistent error envelope for every failure mode."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_error",
                    "message": "request payload failed validation",
                    "details": {"errors": exc.errors()},
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "internal server error",
                    "details": {},
                }
            },
        )


app = create_app()
