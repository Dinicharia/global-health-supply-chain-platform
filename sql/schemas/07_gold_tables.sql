-- =============================================================================
-- Gold Layer: Star Schema
-- =============================================================================
-- Purpose: Dimensional model implementing the Phase 2 star schema design.
--          BI tools (Superset, Power BI) connect directly to these tables.
--
-- Layer contract (Gold): a row only exists here after passing Gate 2
--          (referential integrity, dimensional modeling) -- see Phase 2
--          Medallion Architecture diagram. Every fact row must resolve
--          to a real dimension row; there are no orphaned keys here,
--          by construction (see src/loading/load_gold.py).
--
-- SCD note: dim_supplier, dim_medicine, dim_warehouse are Type 1
--          (overwrite) for now -- see Phase 6 design discussion. A
--          future pass will upgrade specific dimensions to Type 2
--          (historical, effective-dated) once this base model is proven.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- dim_date
-- -----------------------------------------------------------------------------
-- Pre-populated calendar dimension, NOT derived from source data -- a
-- standard data warehousing pattern that lets BI tools do time-based
-- grouping/trending via a simple join instead of reimplementing date
-- math (month/quarter extraction, weekend flags) in every dashboard.
CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key       INTEGER PRIMARY KEY,   -- format: YYYYMMDD, e.g. 20260719 --
                                           -- an integer key is deliberately
                                           -- chosen over the DATE type itself
                                           -- for join performance, a standard
                                           -- Kimball convention
    full_date       DATE NOT NULL UNIQUE,
    day_of_month     INTEGER NOT NULL,
    month             INTEGER NOT NULL,
    month_name         TEXT NOT NULL,
    quarter              INTEGER NOT NULL,
    year                  INTEGER NOT NULL,
    day_of_week             INTEGER NOT NULL,   -- 0=Monday .. 6=Sunday
    day_name                  TEXT NOT NULL,
    is_weekend                  BOOLEAN NOT NULL
);

-- -----------------------------------------------------------------------------
-- dim_country
-- -----------------------------------------------------------------------------
-- Populated from distinct countries observed in dim_supplier. risk_tier
-- is left NULL for now -- FR-09 (country risk score) depends on external
-- data (weather, fuel, port congestion) we haven't ingested yet. This
-- column exists now so fact_shipment can reference it correctly once
-- that external ingestion is built, without a later schema migration.
CREATE TABLE IF NOT EXISTS gold.dim_country (
    country_key SERIAL PRIMARY KEY,
    country_name TEXT NOT NULL UNIQUE,
    risk_tier     TEXT   -- NULL until external risk data is integrated (future phase)
);

-- -----------------------------------------------------------------------------
-- dim_supplier
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.dim_supplier (
    supplier_key SERIAL PRIMARY KEY,
    supplier_id   TEXT NOT NULL UNIQUE,   -- natural key from silver, kept
                                           -- for traceability back to source
    supplier_name  TEXT NOT NULL,
    country_key     INTEGER NOT NULL REFERENCES gold.dim_country(country_key),
    performance_tier TEXT NOT NULL
);

-- -----------------------------------------------------------------------------
-- dim_medicine
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.dim_medicine (
    medicine_key SERIAL PRIMARY KEY,
    medicine_id   TEXT NOT NULL UNIQUE,
    medicine_name  TEXT NOT NULL,
    category         TEXT NOT NULL,
    unit_of_measure   TEXT NOT NULL,
    shelf_life_days     INTEGER NOT NULL
);

-- -----------------------------------------------------------------------------
-- dim_warehouse
-- -----------------------------------------------------------------------------
-- Deliberately thin: our source data only ever provides a warehouse_id
-- string (see sql/schemas/06_silver_tables.sql comment on
-- silver.shipment_records -- no dedicated warehouse master source
-- exists). region/country are left NULL with this documented as a real
-- gap -- a production build would need a genuine warehouse master
-- internal source (see original BRD "Warehouse Locations" data source,
-- never actually built in this project's internal_sources list).
CREATE TABLE IF NOT EXISTS gold.dim_warehouse (
    warehouse_key SERIAL PRIMARY KEY,
    warehouse_id   TEXT NOT NULL UNIQUE,
    region           TEXT,   -- NULL: no warehouse master source exists yet
    country_key       INTEGER REFERENCES gold.dim_country(country_key)  -- nullable, same reason
);

-- -----------------------------------------------------------------------------
-- fact_shipment
-- -----------------------------------------------------------------------------
-- Grain: ONE ROW PER SHIPMENT. This is the single most important line
-- in this file to get right -- every measure below (delay_days,
-- quantity, total_cost) only makes sense because the grain is
-- unambiguous. We do not have PO-line-item granularity in our source
-- data, so "per shipment" is both what the data supports and what the
-- business questions (delay detection, lead time) actually need.
CREATE TABLE IF NOT EXISTS gold.fact_shipment (
    shipment_key BIGSERIAL PRIMARY KEY,
    shipment_id   TEXT NOT NULL UNIQUE,   -- natural key, traceability to source

    -- Dimensional foreign keys
    supplier_key      INTEGER NOT NULL REFERENCES gold.dim_supplier(supplier_key),
    medicine_key        INTEGER NOT NULL REFERENCES gold.dim_medicine(medicine_key),
    warehouse_key         INTEGER NOT NULL REFERENCES gold.dim_warehouse(warehouse_key),
    country_key             INTEGER NOT NULL REFERENCES gold.dim_country(country_key),
    order_date_key            INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    eta_date_key                INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    -- Nullable: a shipment still in transit has no actual_arrival_date_key --
    -- mirrors silver.shipment_records.actual_arrival being nullable (VR-05).
    actual_arrival_date_key       INTEGER REFERENCES gold.dim_date(date_key),

    -- Measures
    quantity       INTEGER NOT NULL,
    -- total_cost/currency carried as-is from Silver -- NOT FX-converted.
    -- A future external exchange_rates ingestion (see config/pipeline_config.yaml
    -- external_sources) will add a cost_usd column here once real FX
    -- rates are available -- deferring rather than faking a conversion.
    total_cost       NUMERIC(12, 2) NOT NULL,
    currency           TEXT NOT NULL,

    -- Pre-computed business metrics (FR-03) -- calculated ONCE here during
    -- the Silver->Gold load, not recomputed by every downstream dashboard.
    -- See Phase 2 star schema discussion: this is the payoff of "business
    -- metric engineering" happening in the pipeline, not in BI tool logic.
    delay_days   INTEGER,   -- NULL if still in transit; see calculate_delay_days()
    is_delayed     BOOLEAN NOT NULL,   -- computed against config's
                                       -- shipment_delay.delay_threshold_days
    status           TEXT NOT NULL
);

-- Index supporting "show me delayed shipments this quarter" -- one of
-- the most common queries this whole platform exists to answer (FR-03).
CREATE INDEX IF NOT EXISTS idx_fact_shipment_is_delayed
    ON gold.fact_shipment (is_delayed);

-- Index supporting "shipments by supplier" queries (FR-04, supplier
-- performance scoring) without scanning the whole fact table.
CREATE INDEX IF NOT EXISTS idx_fact_shipment_supplier_key
    ON gold.fact_shipment (supplier_key);