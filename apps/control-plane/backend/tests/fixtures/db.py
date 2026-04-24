# pattern: Imperative Shell
"""Test fixtures for database setup and teardown.

Provides Postgres testcontainer with pg_uuidv7, migrations, and per-test sessions.
"""

import asyncio
import pathlib

import pytest
from alembic.command import upgrade
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container():
    """Session-scoped Postgres container with pg_uuidv7 extension.

    Uses rac-pg-uuidv7:test image (pre-built from tests/fixtures/postgres/Dockerfile).
    """
    container = PostgresContainer(
        image="rac-pg-uuidv7:test",
        driver="asyncpg",
    )
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def pg_dsn(postgres_container) -> str:
    """Session-scoped Postgres DSN from the testcontainer."""
    url = postgres_container.get_connection_url()
    # Replace psycopg2 with asyncpg for async driver
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def migrated_db(pg_dsn: str) -> str:
    """Session-scoped: runs Alembic migrations on the test DB.

    Installs pg_uuidv7 extension and rac_app role, then runs alembic upgrade.
    Uses asyncpg directly (same pattern as migration test container).
    """
    init_statements = [
        "CREATE EXTENSION IF NOT EXISTS pg_uuidv7",
        """DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_app') THEN
                CREATE ROLE rac_app WITH LOGIN PASSWORD 'rac_app_password';
            ELSE
                ALTER ROLE rac_app WITH LOGIN PASSWORD 'rac_app_password';
            END IF;
        END $$""",
        "GRANT CONNECT ON DATABASE test TO rac_app",
        "GRANT USAGE ON SCHEMA public TO rac_app",
    ]

    async def init_db() -> None:
        engine = create_async_engine(pg_dsn, echo=False)
        async with engine.begin() as conn:
            for stmt in init_statements:
                await conn.execute(text(stmt))
        await engine.dispose()

    asyncio.run(init_db())

    # Run Alembic migrations using asyncpg DSN
    migrations_dir = pathlib.Path(__file__).parent.parent.parent / "migrations"
    alembic_cfg = AlembicConfig(str(migrations_dir.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", pg_dsn)
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    upgrade(alembic_cfg, "head")

    return pg_dsn


@pytest.fixture
async def db_session(migrated_db: str):
    """Function-scoped: provides an AsyncSession with transaction isolation.

    Uses NullPool and SAVEPOINT for each test with automatic rollback on exit.
    NullPool ensures this fixture does not share connections with the app's engine pool.
    """
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        # Start an outer transaction first so SAVEPOINT works
        async with session.begin():
            # Start a nested transaction (SAVEPOINT)
            async with session.begin_nested():
                yield session
            # SAVEPOINT is rolled back on exit
        # Outer transaction rolled back

    await engine.dispose()
