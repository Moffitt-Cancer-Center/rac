# pattern: Imperative Shell
"""Database engine and session management."""

import functools

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rac_control_plane.settings import get_settings


@functools.lru_cache
def get_engine():
    """Create and cache the async SQLAlchemy engine."""
    settings = get_settings()
    return create_async_engine(
        settings.pg_dsn,
        echo=False,
        pool_size=10,
        max_overflow=20,
    )


@functools.lru_cache
def get_session_maker():
    """Create and cache the async sessionmaker."""
    engine = get_engine()
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session():
    """FastAPI dependency: yields an AsyncSession for the request."""
    factory = get_session_maker()
    async with factory() as session:
        yield session
