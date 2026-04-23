-- Initialize Postgres for RAC development

-- Create pg_uuidv7 extension
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;

-- Create application role
CREATE ROLE rac_app LOGIN PASSWORD 'devonly_rac_app';

-- Grant connect to database
GRANT CONNECT ON DATABASE rac TO rac_app;

-- Set search path for rac_app
ALTER ROLE rac_app SET search_path = public;
