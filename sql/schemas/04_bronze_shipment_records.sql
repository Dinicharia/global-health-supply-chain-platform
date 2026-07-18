-- =============================================================================
-- Bronze Table: shipment_records_raw
-- =============================================================================
-- Purpose: Raw landing table for Shipment Records (source format: JSON --
--          config/pipeline_config.yaml -> internal_sources -> shipment_records).
-- Layer contract: see 01_bronze_supplier_master.sql header.
--
-- Business relevance: this is the table FR-03 (shipment delay detection)
--          ultimately depends on -- eta and actual_arrival, still stored
--          as TEXT here, become real DATE comparisons only once this data
--          reaches Silver (Gate 1) and then Gold's fact_shipment table.
-- =============================================================================

CREATE TABLE IF NOT EXISTS bronze.shipment_records_raw (
    bronze_id BIGSERIAL PRIMARY KEY,

    shipment_id     TEXT,
    po_id            TEXT,
    warehouse_id      TEXT,
    eta               TEXT,
    actual_arrival     TEXT,
    transport_company   TEXT,
    status             TEXT,

    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_file TEXT NOT NULL,
    run_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_shipment_records_raw_ingested_at
    ON bronze.shipment_records_raw (ingested_at);