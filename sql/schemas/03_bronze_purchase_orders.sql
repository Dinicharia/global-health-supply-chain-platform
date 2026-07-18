-- =============================================================================
-- Bronze Table: purchase_orders_raw
-- =============================================================================
-- Purpose: Raw landing table for Purchase Orders (source format: JSON --
--          see config/pipeline_config.yaml -> internal_sources -> purchase_orders).
-- Layer contract: see 01_bronze_supplier_master.sql header.
--
-- Note: unlike the CSV sources, this table's shape is driven by JSON keys,
--       not CSV headers -- but the Bronze rule is identical: every field
--       lands as TEXT, no matter the source format. Format doesn't change
--       the layer contract.
-- =============================================================================

CREATE TABLE IF NOT EXISTS bronze.purchase_orders_raw (
    bronze_id BIGSERIAL PRIMARY KEY,

    po_id          TEXT,
    supplier_id    TEXT,
    medicine_id    TEXT,
    order_date     TEXT,
    quantity       TEXT,
    total_cost     TEXT,
    currency       TEXT,

    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_file TEXT NOT NULL,
    run_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchase_orders_raw_ingested_at
    ON bronze.purchase_orders_raw (ingested_at);