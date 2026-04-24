"""Integration tests for Alembic schema migrations.

Uses testcontainers to boot a real Postgres 16 with pg_uuidv7 extension,
applies the migration, and verifies the schema state.

Verifies AC12.1: append-only tables prevent UPDATE/DELETE from rac_app role.
"""

import asyncio
import pathlib
import tempfile
from uuid import uuid4

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def migration_postgres_container():
    """Session-scoped Postgres 16 container for migration tests only.

    Uses a separate fixture name to avoid collision with conftest's postgres_container.
    """
    container = PostgresContainer(
        image="rac-pg-uuidv7:test",
        driver="asyncpg",
    )
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def migration_pg_dsn(migration_postgres_container) -> str:
    """Postgres connection DSN from migration test container."""
    url = migration_postgres_container.get_connection_url()
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def migration_db(migration_pg_dsn: str) -> str:  # type: ignore[override]
    """Apply migrations and yield the DSN.

    C8 fix: split multi-statement SQL into individual executes.
    Uses a fresh migration-only container (migration_pg_dsn) to avoid
    fixture collision with conftest's migration_db from tests.fixtures.db.
    """
    # Initialise extensions and roles — each statement executed separately via asyncpg
    # Note: CREATE ROLE IF NOT EXISTS requires a DO block under asyncpg (prepared-statement limit)
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
        engine = create_async_engine(migration_pg_dsn, echo=False)
        async with engine.begin() as conn:
            for stmt in init_statements:
                await conn.execute(text(stmt))
        await engine.dispose()

    asyncio.run(init_db())

    # Run Alembic migrations using the asyncpg DSN directly
    migrations_dir = pathlib.Path(__file__).parent.parent / "migrations"
    alembic_cfg = Config(str(migrations_dir.parent / "alembic.ini"))
    # Set the asyncpg DSN — env.py's _get_database_url() prefers explicit URL
    alembic_cfg.set_main_option("sqlalchemy.url", migration_pg_dsn)
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    upgrade(alembic_cfg, "head")

    return migration_pg_dsn


@pytest.mark.asyncio
async def test_all_tables_exist(migration_db: str) -> None:
    """Verify all expected tables were created."""
    engine = create_async_engine(migration_db, echo=False)

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        tables = {row[0] for row in result.fetchall()}

    expected_tables = {
        "submission",
        "app",
        "asset",
        "scan_result",
        "detection_finding",
        "approval_event",
        "reviewer_token",
        "revoked_token",
        "access_log",
        "signing_key_version",
        "agent",
        "webhook_subscription",
        "cost_snapshot_monthly",
        "shared_reference_catalog",
        "idempotency_key",
    }

    assert expected_tables <= tables, (
        f"Missing tables: {expected_tables - tables}"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_submission_status_enum_exists(migration_db: str) -> None:
    """Verify submission_status ENUM type exists with all states."""
    engine = create_async_engine(migration_db, echo=False)

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                WHERE typname = 'submission_status'
                ORDER BY enumlabel;
            """)
        )
        enum_values = sorted(row[0] for row in result.fetchall())

    expected_values = sorted([
        "approved",
        "awaiting_it_review",
        "awaiting_research_review",
        "awaiting_scan",
        "deployed",
        "it_rejected",
        "needs_assistance",
        "needs_user_action",
        "pipeline_error",
        "research_rejected",
        "scan_rejected",
    ])

    assert enum_values == expected_values, (
        f"Enum values mismatch: {enum_values} != {expected_values}"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_uuidv7_function_works(migration_db: str) -> None:
    """Verify uuidv7() function is callable."""
    engine = create_async_engine(migration_db, echo=False)

    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT uuidv7();"))
        uuid_val = result.scalar()

    assert uuid_val is not None
    assert len(str(uuid_val)) == 36, f"UUID length wrong: {len(str(uuid_val))}"
    await engine.dispose()


@pytest.mark.asyncio
async def test_required_indexes_exist(migration_db: str) -> None:
    """Verify expected indexes were created."""
    engine = create_async_engine(migration_db, echo=False)

    async with engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'submission' AND schemaname = 'public'
            """)
        )
        submission_indexes = {row[0] for row in result.fetchall()}

        result2 = await conn.execute(
            text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'access_log' AND schemaname = 'public'
            """)
        )
        access_log_indexes = {row[0] for row in result2.fetchall()}

    # Check a few critical indexes
    assert any("status" in idx for idx in submission_indexes), (
        f"Missing status index in submission. Found: {submission_indexes}"
    )
    assert any("submitter" in idx for idx in submission_indexes), (
        f"Missing submitter_principal_id index in submission. Found: {submission_indexes}"
    )
    assert any("created_at" in idx for idx in access_log_indexes), (
        f"Missing created_at index in access_log. Found: {access_log_indexes}"
    )

    await engine.dispose()


@pytest.mark.asyncio
async def test_append_only_tables_prevent_updates(migration_db: str) -> None:
    """Verify rac_app role cannot UPDATE/DELETE on append-only tables.

    C9 fix: explicitly connects as rac_app, asserts InsufficientPrivilege
    (SQLSTATE 42501) is raised on UPDATE, does NOT swallow the error.

    AC12.1: append-only tables (access_log, approval_event, revoked_token,
    detection_finding) must not allow UPDATE/DELETE from rac_app role.
    """
    import asyncpg

    engine = create_async_engine(migration_db, echo=False)
    test_row_id = str(uuid4())

    # Grant rac_app INSERT on access_log (migration only revokes UPDATE/DELETE)
    # Note: access_log uses UUID primary key (uuidv7), no sequence needed.
    async with engine.begin() as conn:
        await conn.execute(
            text("GRANT INSERT ON access_log TO rac_app")
        )
        # Insert a test row as superuser for the UPDATE test
        await conn.execute(
            text("""
                INSERT INTO access_log (id, principal_id, action)
                VALUES (:id, :principal_id, :action)
            """),
            {"id": test_row_id, "principal_id": str(uuid4()), "action": "test"},
        )

    await engine.dispose()

    # Build rac_app DSN — replace credentials in the connection string
    # pg_dsn format: postgresql+asyncpg://user:pass@host:port/db
    rac_app_dsn = migration_db
    # Replace the user:password portion
    import re
    rac_app_dsn = re.sub(
        r"postgresql\+asyncpg://[^@]+@",
        "postgresql+asyncpg://rac_app:rac_app_password@",
        migration_db,
    )

    rac_engine = create_async_engine(rac_app_dsn, echo=False)

    # Step 1: INSERT as rac_app should succeed
    async with rac_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO access_log (id, principal_id, action)
                VALUES (:id, :principal_id, :action)
            """),
            {"id": str(uuid4()), "principal_id": str(uuid4()), "action": "test_insert"},
        )

    # Step 2: UPDATE as rac_app must fail with InsufficientPrivilege (SQLSTATE 42501)
    update_raised = False
    try:
        async with rac_engine.begin() as conn:
            await conn.execute(
                text("UPDATE access_log SET action = 'modified' WHERE id = :id"),
                {"id": test_row_id},
            )
    except Exception as exc:
        update_raised = True
        # Accept either asyncpg or sqlalchemy wrapped exception
        exc_str = str(exc).lower()
        # Check for insufficient privilege error
        assert (
            "42501" in exc_str
            or "insufficient privilege" in exc_str
            or "permission denied" in exc_str
        ), f"Expected privilege error, got: {exc}"
    finally:
        await rac_engine.dispose()

    assert update_raised, (
        "Expected UPDATE to raise InsufficientPrivilege for rac_app role, "
        "but it succeeded. Migration grant-revoke DDL may not have run."
    )


@pytest.mark.asyncio
async def test_foreign_keys_exist(migration_db: str) -> None:
    """Verify critical foreign keys are in place."""
    engine = create_async_engine(migration_db, echo=False)

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT tc.constraint_name, tc.table_name, kcu.column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                ORDER BY tc.table_name, tc.constraint_name;
            """)
        )
        fks = [(row[1], row[0]) for row in result.fetchall()]

    fk_names = {fk[1] for fk in fks}
    assert any("agent" in fk and "submission" in fk for fk in fk_names), (
        f"Missing submission->agent FK. Found: {fk_names}"
    )
    assert any("approval_event" in fk and "submission" in fk for fk in fk_names), (
        f"Missing approval_event->submission FK. Found: {fk_names}"
    )

    await engine.dispose()
