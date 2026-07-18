-- =============================================================================
-- Bronze Table: medicine_catalogue_raw
-- =============================================================================
-- Purpose: Raw landing table for the Medicine Catalogue internal source
--          (config/pipeline_config.yaml -> internal_sources -> medicine_catalogue).
-- Layer contract: see 01_bronze_supplier_master.sql header -- identical
--          Bronze rules apply (all TEXT, nullable source columns, same
--          three audit columns).
-- =============================================================================

CREATE TABLE IF NOT EXISTS bronze.medicine_catalogue_raw (
    bronze_id BIGSERIAL PRIMARY KEY,

    medicine_id     TEXT,
    medicine_name    TEXT,
    category         TEXT,
    unit_of_measure   TEXT,
    -- Stored as TEXT even though this will become an INTEGER in Silver --
    -- see Bronze layer contract: type enforcement happens at Gate 1, not here.
    shelf_life_days   TEXT,

    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_file TEXT NOT NULL,
    run_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_medicine_catalogue_raw_ingested_at
    ON bronze.medicine_catalogue_raw (ingested_at);