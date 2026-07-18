-- =============================================================================
-- Bronze Table: supplier_master_raw
-- =============================================================================
-- Purpose: Raw, unmodified landing table for the Supplier Master internal
--          source (see config/pipeline_config.yaml -> internal_sources ->
--          supplier_master).
--
-- Layer contract (Bronze): every column is TEXT, regardless of the "real"
--          data type, because Bronze must accept a malformed source row
--          rather than reject it -- type/format validation is Silver's job
--          (Gate 1), not Bronze's. See Phase 2 Medallion Architecture.
--
-- Upstream source: data/raw/supplier_master.csv (simulated, since this is
--          a portfolio project -- see BRD Section 7, Assumptions).
-- =============================================================================

CREATE TABLE IF NOT EXISTS bronze.supplier_master_raw (

    -- Surrogate primary key for THIS Bronze table specifically. Not to be
    -- confused with the source system's own supplier_id (below, still
    -- stored as TEXT) -- this key only identifies a row within Bronze.
    bronze_id BIGSERIAL PRIMARY KEY,

    -- ---- Raw source columns, all TEXT, all nullable ----
    -- Nullable because Bronze must accept a row even if a column is
    -- entirely missing -- rejecting it here would violate FR-11
    -- (immutable, complete raw capture).
    supplier_id         TEXT,
    supplier_name        TEXT,
    country              TEXT,
    contact_email         TEXT,
    contract_start_date    TEXT,
    performance_tier       TEXT,

    -- ---- Audit/lineage metadata columns (NOT part of the source data) ----

    -- Exact moment this row was written into Bronze. Distinct from any
    -- "order_date"-style business date -- this is a system timestamp,
    -- hence the _at suffix per our Phase 2 naming convention.
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Which physical file/API response this row came from. This is the
    -- literal answer to "whose fault, and where do I go fix it" from our
    -- Phase 2 discussion on why Bronze needs a source identifier.
    source_file TEXT NOT NULL,

    -- Which pipeline run produced this row. Left nullable for now --
    -- Prefect isn't wired up until Phase 7, so no run_id exists yet.
    -- We're deliberately designing the column ahead of the orchestration
    -- that will populate it, rather than retrofitting it later.
    run_id TEXT
);

-- Index on ingested_at: supports the common query pattern "show me
-- everything loaded in today's run" without a full table scan. Every
-- index in this project is commented with the query pattern it serves,
-- per our Phase 2 coding standards -- an unexplained index is technical
-- debt six months from now.
CREATE INDEX IF NOT EXISTS idx_supplier_master_raw_ingested_at
    ON bronze.supplier_master_raw (ingested_at);