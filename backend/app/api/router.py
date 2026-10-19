"""Aggregate router — the single place the API prefix/tags are assembled."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import analysis, creator, engine_trace, health, report, sample, team, upload

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(upload.router)
api_router.include_router(sample.router)
api_router.include_router(analysis.router)
api_router.include_router(report.router)
api_router.include_router(engine_trace.router)
api_router.include_router(team.router)
api_router.include_router(creator.router)
