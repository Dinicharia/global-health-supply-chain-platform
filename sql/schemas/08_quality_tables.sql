-- =============================================================================
-- Data Quality Framework: Schema and Results Table
-- =============================================================================
-- Purpose: Implements Phase 8 -- automated, post-pipeline health checks
--          answering "is the platform behaving normally today," distinct
--          from Gate 1's row-level validation (see docs/silver_validation_rules.md).
--
-- Design: results land in a real table (not just log lines) so quality
--          trends are queryable over time, not just visible in a single
--          run's console output. Mirrors the same reasoning behind
--          silver.quarantine existing as a table rather than a log stream.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS quality;

-- -----------------------------------------------------------------------------
-- quality.check_results
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quality.check_results (
    check_result_id BIGSERIAL PRIMARY KEY,

    -- Which run this check belongs to -- lets us group all checks from
    -- one pipeline execution together, same run_id convention used
    -- throughout extraction/silver loading.
    run_id      TEXT NOT NULL,

    -- e.g. 'freshness', 'completeness', 'uniqueness', 'consistency' --
    -- matches the four categories from the Phase 8 design discussion.
    check_category TEXT NOT NULL,

    -- A specific, stable identifier for this exact check, e.g.
    -- 'FRESH-01', 'COMPLETE-03' -- mirrors the VR-NN convention from
    -- docs/silver_validation_rules.md, so checks are referenceable the
    -- same way validation rules are.
    check_id     TEXT NOT NULL,

    -- Human-readable description of what this check verifies.
    description   TEXT NOT NULL,

    -- 'PASS' or 'FAIL' -- the check's binary outcome.
    status          TEXT NOT NULL CHECK (status IN ('PASS', 'FAIL')),

    -- 'INFO', 'WARNING', 'CRITICAL' -- only meaningful when status = 'FAIL'.
    -- Lets a future alerting layer distinguish "worth noting" from
    -- "wake someone up," without a schema change when that's built.
    severity         TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),

    -- Free-form details -- e.g. the actual vs expected row count, the
    -- specific duplicate key found. Stored as JSONB so different check
    -- types can carry different shaped detail without needing a new
    -- column per check type.
    details            JSONB,

    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Supports "show me every FAILED check from the last run" and "show me
-- how check X has trended over the last 30 days" -- the two query
-- patterns a real quality report/dashboard would run constantly.
CREATE INDEX IF NOT EXISTS idx_check_results_run_id
    ON quality.check_results (run_id);
CREATE INDEX IF NOT EXISTS idx_check_results_check_id_checked_at
    ON quality.check_results (check_id, checked_at);