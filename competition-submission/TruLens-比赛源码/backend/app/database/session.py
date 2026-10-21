"""Async engine + session factory.

One engine per process. ``expire_on_commit=False`` so ORM objects stay usable
after commit (important: services return objects built inside a session).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

# SQLite needs a couple of knobs to behave under async concurrency; they are
# ignored for other dialects.
_connect_args: dict = {}
if _settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.sql_echo,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

# Session factory. Services that outlive a request (background tasks) build
# their own sessions from this instead of borrowing the request session.
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a per-request session."""
    async with SessionLocal() as session:
        yield session
