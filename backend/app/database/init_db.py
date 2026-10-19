"""Schema bootstrap.

For the hackathon we create tables directly via ``create_all``. The moment the
schema needs to evolve against a live PostgreSQL, switch to Alembic — the
naming convention in :mod:`app.database.base` already makes that painless.
"""

from __future__ import annotations

import logging

from app.database.base import Base
from app.database.session import engine

logger = logging.getLogger(__name__)


async def init_db() -> None:
    # Import models so every table is registered on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ready (%d tables)", len(Base.metadata.tables))


async def dispose_db() -> None:
    await engine.dispose()
