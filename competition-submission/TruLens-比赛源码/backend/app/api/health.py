"""Health & capability probes (used by docker healthchecks and the frontend)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.api.deps import get_analysis_service
from app.core.config import get_settings
from app.core.enums import DIMENSION_ORDER
from app.database.session import engine

router = APIRouter(tags=["system"])


@router.get("/health", summary="健康检查")
async def health(request: Request) -> dict:
    settings = get_settings()
    service = get_analysis_service(request)
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
        "database": engine.dialect.name,
        "llm_provider": settings.llm_provider,
        "llm_enabled": settings.enable_llm,
        # Which backend currently produces the analysis dimensions. When this is
        # "trust_engine", GET /api/engine-trace/{task_id} has data.
        "analysis_backend": "trust_engine" if service.engine_runner else "heuristics",
        # Whether pasting a Xiaohongshu link can be crawled right now.
        "xhs_enabled": bool(request.app.state.xhs_service.available),
    }


@router.get("/agents", summary="列出可用 Agent 及其职责")
async def list_agents(request: Request) -> dict:
    """Exposes the agent workflow so the UI (and judges) can see the pipeline.

    Each entry carries the frontend's ``code`` (T/R/U/S) and ``label`` alongside
    the stable internal ``name``.
    """
    service = get_analysis_service(request)
    return {
        "pipeline": [*DIMENSION_ORDER, "report"],
        "dimensions": service.registry.describe(),
    }
