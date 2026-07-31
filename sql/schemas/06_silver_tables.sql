-- =============================================================================
-- Silver Layer Tables
-- =============================================================================
-- Purpose: Validated, typed, business-rule-checked versions of the five
--          Bronze sources, plus a shared quarantine table for rejected rows.
--          See docs/silver_validation_rules.md (VR-01 through VR-10) for
--          the full rule catalogue each table's loader must enforce.
--
-- Layer contract (Silver): unlike Bronze, columns here use REAL types
--          (INTEGER, DATE, NUMERIC) -- a row only exists here after
--          successfully parsing into these types AND passing every
--          applicable business rule. This is Gate 1 from the Phase 2
--          Medallion Architecture diagram, made concrete.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- silver.supplier_master
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.supplier_master (
    supplier_id         TEXT PRIMARY KEY,   -- natural key from source; still
                                             -- TEXT because "SUP-0001" is a
                                             -- business identifier, not a number
    supplier_name        TEXT NOT NULL,
    country               TEXT NOT NULL,
    -- Nullable: per VR-01, a missing email is valid business data, not
    -- a validation failure -- see rule catalogue.
    contact_email          TEXT,
    contract_start_date     DATE NOT NULL,
    performance_tier         TEXT NOT NULL,

    -- Lineage back to Bronze -- lets us trace any Silver row to the exact
    -- Bronze row(s) it was derived from (NFR-04, auditability).
    bronze_id     BIGINT NOT NULL,
    validated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- silver.medicine_catalogue
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.medicine_catalogue (
    medicine_id     TEXT PRIMARY KEY,
    medicine_name    TEXT NOT NULL,
    category          TEXT NOT NULL,
    unit_of_measure    TEXT NOT NULL,
    shelf_life_days     INTEGER NOT NULL,   -- real INTEGER now -- this is
                                             -- exactly the type promotion
                                             -- Bronze deliberately deferred

    bronze_id     BIGINT NOT NULL,
    validated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- silver.purchase_orders
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.purchase_orders (
    po_id          TEXT PRIMARY KEY,
    -- Real foreign key constraint -- this is Silver actually ENFORCING
    -- VR-02 at the database level, not just in application code. A row
    -- with an orphaned supplier_id literally cannot be inserted here.
    supplier_id    TEXT NOT NULL REFERENCES silver.supplier_master(supplier_id),
    medicine_id    TEXT NOT NULL REFERENCES silver.medicine_catalogue(medicine_id),
    order_date     DATE NOT NULL,
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    total_cost     NUMERIC(12, 2) NOT NULL CHECK (total_cost >= 0),
    currency       TEXT NOT NULL,

    bronze_id     BIGINT NOT NULL,
    validated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- silver.shipment_records
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.shipment_records (
    shipment_id       TEXT PRIMARY KEY,
    po_id              TEXT NOT NULL REFERENCES silver.purchase_orders(po_id),
    warehouse_id        TEXT NOT NULL,   -- no FK yet -- we don't have a
                                          -- silver.warehouse table (no
                                          -- dedicated Bronze source for
                                          -- it either -- noted as a real
                                          -- gap, acceptable for this
                                          -- portfolio scope, would be a
                                          -- genuine internal source in
                                          -- production)
    eta                 DATE NOT NULL,
    -- Nullable: per VR-05, NULL means "still in transit" -- a valid state.
    actual_arrival        DATE,
    transport_company      TEXT NOT NULL,
    status                  TEXT NOT NULL,

    bronze_id     BIGINT NOT NULL,
    validated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- silver.warehouse_inventory
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.warehouse_inventory (
    -- Composite natural key: no single inventory_id exists in the source,
    -- and a (warehouse, medicine, batch) triple is what's actually unique
    -- in the real world -- two different batches of the same medicine can
    -- coexist in the same warehouse.
    warehouse_id       TEXT NOT NULL,
    medicine_id         TEXT NOT NULL REFERENCES silver.medicine_catalogue(medicine_id),
    batch_number          TEXT NOT NULL,
    -- CHECK enforces VR-07 (no negative stock) at the database level.
    quantity_on_hand        INTEGER NOT NULL CHECK (quantity_on_hand >= 0),
    expiry_date               DATE NOT NULL,

    bronze_id     BIGINT NOT NULL,
    validated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (warehouse_id, medicine_id, batch_number)
);

-- -----------------------------------------------------------------------------
-- silver.quarantine
-- -----------------------------------------------------------------------------
-- Shared across every source (see design rationale: quarantine QUESTIONS
-- are source-agnostic even though quarantined ROWS are shaped differently).
CREATE TABLE IF NOT EXISTS silver.quarantine (
    quarantine_id BIGSERIAL PRIMARY KEY,

    -- Which source/table this row was headed for -- lets us query
    -- "show me all shipment_records rejections" without a UNION.
    source_name    TEXT NOT NULL,
    target_table    TEXT NOT NULL,

    -- The rule that rejected this row (e.g. 'VR-02') -- direct link back
    -- to the rule catalogue, so a failure is never a mystery.
    rule_id          TEXT NOT NULL,
    rule_description  TEXT NOT NULL,

    -- The full offending row, preserved as JSON -- unlike a rejected SQL
    -- INSERT (which would just vanish), we keep the ENTIRE original row
    -- here, so an analyst can see exactly what was wrong without cross-
    -- referencing back into Bronze.
    row_data           JSONB NOT NULL,

    -- Lineage: which Bronze row and which pipeline run produced this
    -- rejection (NFR-04 again -- traceability doesn't stop at Silver).
    bronze_id     BIGINT,
    run_id         TEXT,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index supporting "how many rejections of each rule fired today" --
-- the core query our future Data Quality monitoring (Phase 8) will run
-- constantly.
CREATE INDEX IF NOT EXISTS idx_quarantine_rule_id_quarantined_at
    ON silver.quarantine (rule_id, quarantined_at);