# pattern: Imperative Shell
"""Test fixtures for database setup and teardown.

Provides Postgres testcontainer with pg_uuidv7, migrations, and per-test sessions.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container():
    """Session-scoped Postgres container with pg_uuidv7 extension.

    Builds custom image with pg_uuidv7 from the Dockerfile, then starts it.
    """
    # Path to the Dockerfile
    dockerfile_dir = Path(__file__).parent / "postgres"
    dockerfile_path = dockerfile_dir / "Dockerfile"

    # Build the image using Docker
    import docker

    client = docker.from_env()

    # Build custom image with pg_uuidv7
    try:
        image, _ = client.images.build(
            path=str(dockerfile_dir),
            dockerfile="Dockerfile",
            tag="pg-uuidv7:test",
            rm=True,
        )
    except docker.errors.BuildError as e:
        pytest.skip(f"Could not build pg_uuidv7 image: {e}")

    # Create init SQL for the container
    init_sql = """
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;
CREATE ROLE rac_app WITH LOGIN PASSWORD 'rac_app_password';
GRANT USAGE ON SCHEMA public TO rac_app;
GRANT CREATE ON SCHEMA public TO rac_app;
"""

    # Write init SQL to temp location for mounting
    init_dir = Path("/tmp/rac_db_init")
    init_dir.mkdir(exist_ok=True, parents=True)
    init_file = init_dir / "01-init.sql"
    init_file.write_text(init_sql)

    # Start container using the custom image
    container = (
        PostgresContainer(image="pg-uuidv7:test")
        .with_env("POSTGRES_DB", "rac_test")
        .with_env("POSTGRES_USER", "postgres")
        .with_env("POSTGRES_PASSWORD", "postgres")
        .with_bind(str(init_dir), "/docker-entrypoint-initdb.d", "ro")
    )

    container.start()

    yield container

    container.stop()


@pytest.fixture(scope="session")
def pg_dsn(postgres_container) -> str:
    """Session-scoped Postgres DSN from the testcontainer."""
    url = postgres_container.get_connection_url()
    # Replace psycopg2 with asyncpg for async driver
    return url.replace("psycopg2", "asyncpg", 1)


@pytest.fixture(scope="session")
def migrated_db(pg_dsn):
    """Session-scoped: runs Alembic migrations on the test DB."""

    def run_alembic_sync():
        """Run migrations synchronously."""
        # Find the migrations directory
        migrations_dir = Path(__file__).parent.parent.parent.parent / "migrations"

        # Build the sync DSN for Alembic
        sync_dsn = pg_dsn.replace("asyncpg", "psycopg2", 1)

        alembic_cfg = AlembicConfig(str(migrations_dir.parent / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", sync_dsn)
        alembic_cfg.set_main_option("script_location", str(migrations_dir))

        # Run upgrade
        command.upgrade(alembic_cfg, "head")

    # Run migrations before yielding
    run_alembic_sync()

    yield pg_dsn


@pytest.fixture
async def db_session(migrated_db, pg_dsn):
    """Function-scoped: provides an AsyncSession with transaction isolation.

    Uses SAVEPOINT for each test with automatic rollback on exit.
    """
    engine = create_async_engine(pg_dsn, echo=False)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        # Start a nested transaction (SAVEPOINT)
        async with session.begin_nested():
            yield session
        # Automatic rollback on exit

    await engine.dispose()
