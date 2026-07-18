-- =============================================================================
-- Bronze Table: warehouse_inventory_raw
-- =============================================================================
-- Purpose: Raw landing table for Warehouse Inventory (source format: CSV --
--          config/pipeline_config.yaml -> internal_sources -> warehouse_inventory).
-- Layer contract: see 01_bronze_supplier_master.sql header.
--
-- Business relevance: this table ultimately feeds FR-05 (expiry risk) and
--          FR-06 (inventory threshold breaches) -- expiry_date and
--          quantity_on_hand stay as TEXT here on purpose; a malformed
--          quantity value (e.g. a stray comma from a bad CSV export)
--          must not cause Bronze itself to reject the row.
-- =============================================================================

CREATE TABLE IF NOT EXISTS bronze.warehouse_inventory_raw (
    bronze_id BIGSERIAL PRIMARY KEY,

    warehouse_id       TEXT,
    medicine_id         TEXT,
    quantity_on_hand      TEXT,
    expiry_date           TEXT,
    batch_number           TEXT,

    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_file TEXT NOT NULL,
    run_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_warehouse_inventory_raw_ingested_at
    ON bronze.warehouse_inventory_raw (ingested_at);