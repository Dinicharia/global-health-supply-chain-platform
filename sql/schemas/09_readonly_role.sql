-- =============================================================================
-- Read-Only Role for BI Tools (Apache Superset, Power BI)
-- =============================================================================
-- Purpose: Implements the Phase 2 Security Strategy decision -- BI tools
--          connect with a role that is ARCHITECTURALLY incapable of
--          writing, not merely policy-restricted. A bug or malicious
--          query originating from a dashboard tool can never corrupt
--          our data, because the database itself refuses any write
--          from this role.
--
-- Scope: SELECT-only on gold (primary BI target) and silver (occasional
--        drill-down/debugging queries an analyst might reasonably run).
--        Deliberately NOT granted on bronze (raw, not analyst-facing)
--        or quality (operational, not business-facing).
-- =============================================================================

-- Create the role if it doesn't already exist -- idempotent, safe to
-- re-run, same principle as every other schema script in this project.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bi_reader') THEN
        CREATE ROLE bi_reader WITH LOGIN PASSWORD 'changeme';
    END IF;
END
$$;

-- Grant CONNECT on the database itself -- without this, the role can't
-- even open a connection, regardless of table-level grants.
GRANT CONNECT ON DATABASE supply_chain_platform TO bi_reader;

-- Grant USAGE on the schemas -- without this, the role can see that
-- tables exist but cannot query them; USAGE is the "can look inside
-- this schema at all" permission, separate from per-table SELECT.
GRANT USAGE ON SCHEMA gold TO bi_reader;
GRANT USAGE ON SCHEMA silver TO bi_reader;

-- SELECT-only on every EXISTING table in gold/silver.
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA silver TO bi_reader;

-- Critical: also grant SELECT on any FUTURE tables created in these
-- schemas. Without this, a new table added later (e.g. a new gold
-- dimension) would silently be invisible to bi_reader until someone
-- remembered to grant it manually -- a real, easy-to-forget gap this
-- line closes permanently.
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT SELECT ON TABLES TO bi_reader;