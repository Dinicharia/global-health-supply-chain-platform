-- =============================================================================
-- Create Medallion Architecture Schemas
-- =============================================================================
-- Purpose: Establishes the three top-level schemas that implement our
--          Medallion architecture (Phase 2 design decision). Every table
--          in this project belongs to exactly one of these three schemas,
--          reflecting its position in the Bronze -> Silver -> Gold data
--          lifecycle, not just an arbitrary grouping.
--
-- Run order: this file must execute FIRST, before any table-creation
--            script, since CREATE TABLE requires its target schema to
--            already exist.
-- =============================================================================

-- Bronze: raw, immutable, as-received data. No transformation logic touches
-- this schema — see Phase 2 Medallion Architecture diagram, Gate 1 sits
-- AFTER this layer, not before it.
CREATE SCHEMA IF NOT EXISTS bronze;

-- Silver: validated, cleaned, deduplicated, normalized data. A record only
-- exists here after passing Gate 1 (schema, null, duplicate, business-rule
-- checks) — see Phase 2 Data Quality discussion.
CREATE SCHEMA IF NOT EXISTS silver;

-- Gold: dimensional model (facts + dimensions), BI-ready. A record only
-- exists here after passing Gate 2 (referential integrity, dimensional
-- modeling) — this is what Superset and Power BI connect to directly.
CREATE SCHEMA IF NOT EXISTS gold;

-- Why IF NOT EXISTS: makes this script safely re-runnable (idempotent),
-- same principle as the `mkdir -p` decision back in Phase 3 — running
-- this twice against a fresh or partially-set-up database should never
-- error out.