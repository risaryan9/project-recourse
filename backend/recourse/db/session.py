"""Async engine and sessions (psycopg 3 driver, `postgresql+psycopg://` URLs)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from recourse.config import Settings, get_settings


def make_engine(url: str | None = None, *, settings: Settings | None = None) -> AsyncEngine:
    """Create an engine for `url` (default: `settings.database_url`)."""
    url = url or (settings or get_settings()).database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see README, 'Database (Neon)')")
    return create_async_engine(
        url,
        # Neon suspends idle compute; check connections before use and recycle them well
        # within its idle timeout.
        pool_pre_ping=True,
        pool_recycle=240,
        pool_size=5,
        max_overflow=5,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit (handy in API handlers).
    return async_sessionmaker(engine, expire_on_commit=False)
