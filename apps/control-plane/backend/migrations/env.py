# pattern: Imperative Shell
"""Alembic env script for async Postgres migrations."""

import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from alembic import context

from rac_control_plane.data.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    """Get database URL: prefer explicitly set URL in alembic config, fall back to settings."""
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    # Fall back to application settings when no URL is explicitly configured
    from rac_control_plane.settings import get_settings
    settings = get_settings()
    return settings.pg_dsn


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    sqlalchemy_url = _get_database_url()
    context.configure(
        url=sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    """Run migrations in async 'online' mode."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create async engine and run migrations."""
    db_url = _get_database_url()

    connectable: AsyncEngine = create_async_engine(
        db_url,
        poolclass=pool.NullPool,
    )

    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
