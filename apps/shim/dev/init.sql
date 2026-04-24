-- Local dev bootstrap SQL
-- Runs once when the Postgres container is first initialised.
-- Creates the pg_uuidv7 extension and the rac_shim role so the shim can
-- connect before Alembic migrations are applied.
--
-- After this script runs, execute Alembic migrations from the control-plane
-- project to create all tables:
--
--   cd apps/control-plane/backend
--   uv run alembic upgrade head

CREATE EXTENSION IF NOT EXISTS pg_uuidv7;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_shim') THEN
        CREATE ROLE rac_shim WITH LOGIN PASSWORD 'rac_shim_dev';
    END IF;
END $$;

GRANT CONNECT ON DATABASE rac_dev TO rac_shim;
GRANT USAGE ON SCHEMA public TO rac_shim;
