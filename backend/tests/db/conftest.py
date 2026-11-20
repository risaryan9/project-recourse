"""Fixtures for database tests against the Neon "test" branch (TEST_DATABASE_URL).

Run with `uv run pytest -m db`. The schema is rebuilt from the migrations once per session
(downgrade to base, then upgrade to head), and every test starts with empty tables.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.config import Settings
from recourse.db.session import make_engine, make_session_factory

ALEMBIC_INI = "alembic.ini"


def _test_url() -> str:
    url = Settings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if url == Settings().database_url:
        pytest.fail("TEST_DATABASE_URL must point at a different branch than DATABASE_URL")
    return url


def alembic_config(url: str) -> Config:
    cfg = Config(ALEMBIC_INI)
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    url = _test_url()
    cfg = alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield url


@pytest.fixture
async def sessions(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory on the test branch; every table is emptied first."""
    sync = create_engine(db_url)
    with sync.begin() as conn:
        conn.execute(text("TRUNCATE cases, gmail_connections CASCADE"))
    sync.dispose()

    engine = make_engine(db_url)
    yield make_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def session(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as s:
        yield s
