-- =============================================================================
-- Gold Layer: Reporting Views for BI Tools
-- =============================================================================
-- Purpose: Pre-joined, denormalized views serving as the single source
--          of business logic for BOTH Apache Superset (Phase 11) and
--          Power BI (Phase 12). Defining joins here, once, in version-
--          controlled SQL, means every BI tool sees identical business
--          logic -- no risk of two dashboards quietly disagreeing about
--          what "delayed" or "on time" means.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- gold.vw_shipment_details
-- -----------------------------------------------------------------------------
-- The primary reporting view: one row per shipment, every dimension
-- already joined and flattened. This is the dataset most dashboard
-- charts will query directly -- a single, wide, denormalized table is
-- exactly what BI tools are optimized to consume (star-schema joins
-- happening at query time, in every chart, would be slower and would
-- duplicate join logic across dozens of charts).
CREATE OR REPLACE VIEW gold.vw_shipment_details AS
SELECT
    f.shipment_key,
    f.shipment_id,
    sup.supplier_name,
    sup.performance_tier,
    c.country_name,
    med.medicine_name,
    med.category AS medicine_category,
    wh.warehouse_id,
    d_order.full_date AS order_date,
    d_eta.full_date AS eta_date,
    d_arrival.full_date AS actual_arrival_date,
    f.quantity,
    f.total_cost,
    f.currency,
    f.delay_days,
    f.is_delayed,
    f.status,
    d_eta.year AS eta_year,
    d_eta.month AS eta_month,
    d_eta.quarter AS eta_quarter,
    d_eta.month_name AS eta_month_name
FROM gold.fact_shipment f
JOIN gold.dim_supplier sup ON sup.supplier_key = f.supplier_key
JOIN gold.dim_country c ON c.country_key = f.country_key
JOIN gold.dim_medicine med ON med.medicine_key = f.medicine_key
JOIN gold.dim_warehouse wh ON wh.warehouse_key = f.warehouse_key
JOIN gold.dim_date d_order ON d_order.date_key = f.order_date_key
JOIN gold.dim_date d_eta ON d_eta.date_key = f.eta_date_key
LEFT JOIN gold.dim_date d_arrival ON d_arrival.date_key = f.actual_arrival_date_key;
-- LEFT JOIN on arrival date specifically: a shipment still in transit
-- has no actual_arrival_date_key (NULL) -- an INNER JOIN here would
-- silently DROP every in-transit shipment from this view entirely,
-- which would be a serious, silent undercounting bug in any dashboard
-- built on top of it.

-- -----------------------------------------------------------------------------
-- gold.vw_supplier_performance
-- -----------------------------------------------------------------------------
-- Pre-aggregated supplier scorecard -- implements FR-04 (supplier
-- performance scoring) as a queryable view rather than logic every
-- chart has to reimplement.
CREATE OR REPLACE VIEW gold.vw_supplier_performance AS
SELECT
    sup.supplier_name,
    sup.performance_tier,
    c.country_name,
    COUNT(*) AS total_shipments,
    SUM(CASE WHEN f.is_delayed THEN 1 ELSE 0 END) AS delayed_shipments,
    ROUND(
        100.0 * SUM(CASE WHEN f.is_delayed THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
        1
    ) AS delay_rate_pct,
    ROUND(AVG(f.delay_days) FILTER (WHERE f.delay_days IS NOT NULL), 1) AS avg_delay_days,
    SUM(f.total_cost) AS total_procurement_cost
FROM gold.fact_shipment f
JOIN gold.dim_supplier sup ON sup.supplier_key = f.supplier_key
JOIN gold.dim_country c ON c.country_key = f.country_key
GROUP BY sup.supplier_name, sup.performance_tier, c.country_name;

-- -----------------------------------------------------------------------------
-- gold.vw_medicine_expiry_risk
-- -----------------------------------------------------------------------------
-- Implements FR-05 (medicines expiring within N days) directly against
-- Silver inventory data (Gold has no dedicated inventory fact table --
-- a reasonable future extension, not built this project -- so this
-- view reads from silver.warehouse_inventory directly, joined against
-- gold.dim_medicine for the category/name enrichment a dashboard needs).
CREATE OR REPLACE VIEW gold.vw_medicine_expiry_risk AS
SELECT
    inv.warehouse_id,
    med.medicine_name,
    med.category,
    inv.batch_number,
    inv.quantity_on_hand,
    inv.expiry_date,
    (inv.expiry_date - CURRENT_DATE) AS days_until_expiry,
    CASE
        WHEN inv.expiry_date < CURRENT_DATE THEN 'Expired'
        WHEN inv.expiry_date - CURRENT_DATE <= 90 THEN 'At Risk (within 90 days)'
        ELSE 'OK'
    END AS expiry_status
FROM silver.warehouse_inventory inv
JOIN gold.dim_medicine med ON med.medicine_id = inv.medicine_id;

-- -----------------------------------------------------------------------------
-- Grants: bi_reader needs explicit SELECT on views too
-- -----------------------------------------------------------------------------
-- Views are distinct database objects from the tables they read --
-- ALTER DEFAULT PRIVILEGES from sql/schemas/09_readonly_role.sql only
-- covers TABLES, so these views need their own explicit grant.
GRANT SELECT ON gold.vw_shipment_details TO bi_reader;
GRANT SELECT ON gold.vw_supplier_performance TO bi_reader;
GRANT SELECT ON gold.vw_medicine_expiry_risk TO bi_reader;