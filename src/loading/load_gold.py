"""
Populates the Gold layer star schema from Silver.

Purpose:
    Implements Gate 2 (Silver -> Gold) from the Phase 2 Medallion
    Architecture: dimensional modeling and referential integrity. Every
    dimension is loaded first, in dependency order, then fact_shipment
    last -- since it references every other dimension's surrogate key.

Design pattern:
    dim_date is generated independently of any source data (a standard
    pre-populated calendar dimension). All other dimensions and the fact
    table are derived from silver.* tables. Dimensions are Type 1
    (overwrite) for now -- see Phase 6 design discussion on why Type 2
    (SCD) is deliberately deferred.
"""

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import Engine, text

from src.utils.config import load_pipeline_config
from src.utils.db import get_engine
from src.utils.logging_setup import get_logger, setup_logging

logger = get_logger("pipeline")

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def calculate_delay_days(eta: date, actual_arrival: date | None) -> int | None:
    """
    Calculate the number of days a shipment arrived after its ETA.

    Business logic: a shipment that hasn't arrived yet (actual_arrival is
    None) has no delay value yet -- we return None rather than 0, because
    0 would falsely imply "on time" for a shipment still in transit.

    This is the exact function designed as a worked example back in
    Phase 2's coding standards discussion -- now wired into the real
    pipeline for the first time.

    Args:
        eta: The originally estimated time of arrival.
        actual_arrival: The actual arrival date, or None if still in transit.

    Returns:
        Number of days late (positive int), 0 if on-time or early,
        or None if the shipment hasn't arrived yet.
    """
    if actual_arrival is None:
        return None
    return max((actual_arrival - eta).days, 0)


def build_date_dimension_rows(start_year: int, end_year: int) -> list[dict]:
    """
    Build the full list of calendar dimension rows for a year range, as
    plain dicts -- no database involved.

    Design note (refactor):
        Deliberately separated from populate_dim_date()'s database write
        so this logic is unit-testable with plain Python assertions,
        matching the same pure-function pattern established in
        src/transformation/validators.py. A bug in date-key formatting,
        quarter calculation, or weekend detection should be catchable
        without a live database connection.

    Args:
        start_year: First year to include (Jan 1).
        end_year: Last year to include (Dec 31), inclusive.

    Returns:
        List of dicts, one per calendar day, matching gold.dim_date's
        columns exactly.
    """
    rows = []
    current = date(start_year, 1, 1)
    end = date(end_year, 12, 31)

    while current <= end:
        rows.append({
            "date_key": int(current.strftime("%Y%m%d")),
            "full_date": current,
            "day_of_month": current.day,
            "month": current.month,
            "month_name": MONTH_NAMES[current.month - 1],
            "quarter": (current.month - 1) // 3 + 1,
            "year": current.year,
            "day_of_week": current.weekday(),
            "day_name": DAY_NAMES[current.weekday()],
            "is_weekend": current.weekday() >= 5,
        })
        current += timedelta(days=1)

    return rows


def populate_dim_date(engine: Engine, start_year: int = 2023, end_year: int = 2027) -> int:
    """
    Generate and load a complete calendar dimension covering start_year
    through end_year, inclusive.

    Business/architecture context:
        Independent of any source data -- see module docstring. Range
        chosen to comfortably cover our sample data's order_date/eta
        spread (contract_start_date goes back ~3 years, eta goes up to
        ~60 days forward from generation time).

    Returns:
        Number of date rows inserted (skips re-inserting existing dates,
        so this function is safely re-runnable).
    """
    rows = build_date_dimension_rows(start_year, end_year)
    df = pd.DataFrame(rows)

    with engine.begin() as conn:
        for _, row in df.iterrows():
            conn.execute(
                text("""
                    INSERT INTO gold.dim_date
                        (date_key, full_date, day_of_month, month, month_name,
                         quarter, year, day_of_week, day_name, is_weekend)
                    VALUES
                        (:date_key, :full_date, :day_of_month, :month, :month_name,
                         :quarter, :year, :day_of_week, :day_name, :is_weekend)
                    ON CONFLICT (date_key) DO NOTHING
                """),
                row.to_dict(),
            )
    return len(df)


def populate_dim_country(engine: Engine) -> int:
    """
    Populate dim_country from distinct countries in silver.supplier_master.

    Business/architecture context:
        risk_tier is left NULL here -- see sql/schemas/07_gold_tables.sql
        comment. This function only knows about countries that appear as
        a SUPPLIER's country; it does not yet know about warehouse
        countries (dim_warehouse.country_key is populated as NULL, a
        documented gap).
    """
    with engine.connect() as conn:
        countries = pd.read_sql(
            text("SELECT DISTINCT country FROM silver.supplier_master"), conn
        )["country"]

    with engine.begin() as conn:
        for country in countries:
            conn.execute(
                text("""
                    INSERT INTO gold.dim_country (country_name, risk_tier)
                    VALUES (:country_name, NULL)
                    ON CONFLICT (country_name) DO NOTHING
                """),
                {"country_name": country},
            )
    return len(countries)


def populate_dim_supplier(engine: Engine) -> int:
    """
    Populate dim_supplier from silver.supplier_master.

    Design note: Type 1 (overwrite) -- see Phase 6 design discussion.
    ON CONFLICT ... DO UPDATE means re-running this after a supplier's
    tier changes in Silver OVERWRITES the Gold row rather than creating
    a new version. This is a deliberate simplification for now: we lose
    the ability to answer "what was this supplier's tier last quarter,"
    which a future Type 2 upgrade would restore.
    """
    with engine.connect() as conn:
        suppliers = pd.read_sql(
            text("""
                SELECT s.supplier_id, s.supplier_name, s.performance_tier, c.country_key
                FROM silver.supplier_master s
                JOIN gold.dim_country c ON c.country_name = s.country
            """),
            conn,
        )

    with engine.begin() as conn:
        for _, row in suppliers.iterrows():
            conn.execute(
                text("""
                    INSERT INTO gold.dim_supplier
                        (supplier_id, supplier_name, country_key, performance_tier)
                    VALUES
                        (:supplier_id, :supplier_name, :country_key, :performance_tier)
                    ON CONFLICT (supplier_id) DO UPDATE SET
                        supplier_name = EXCLUDED.supplier_name,
                        country_key = EXCLUDED.country_key,
                        performance_tier = EXCLUDED.performance_tier
                """),
                row.to_dict(),
            )
    return len(suppliers)


def populate_dim_medicine(engine: Engine) -> int:
    """Populate dim_medicine from silver.medicine_catalogue. Type 1 (overwrite)."""
    with engine.connect() as conn:
        medicines = pd.read_sql(text("SELECT * FROM silver.medicine_catalogue"), conn)

    with engine.begin() as conn:
        for _, row in medicines.iterrows():
            conn.execute(
                text("""
                    INSERT INTO gold.dim_medicine
                        (medicine_id, medicine_name, category, unit_of_measure, shelf_life_days)
                    VALUES
                        (:medicine_id, :medicine_name, :category, :unit_of_measure, :shelf_life_days)
                    ON CONFLICT (medicine_id) DO UPDATE SET
                        medicine_name = EXCLUDED.medicine_name,
                        category = EXCLUDED.category,
                        unit_of_measure = EXCLUDED.unit_of_measure,
                        shelf_life_days = EXCLUDED.shelf_life_days
                """),
                {k: v for k, v in row.to_dict().items() if k != "bronze_id" and k != "validated_at"},
            )
    return len(medicines)


def populate_dim_warehouse(engine: Engine) -> int:
    """
    Populate dim_warehouse from distinct warehouse_ids in silver.shipment_records.

    Business/architecture context: region/country_key are left NULL --
    see sql/schemas/07_gold_tables.sql comment. We have no warehouse
    master source, only the warehouse_id string as it appears in
    shipment records.
    """
    with engine.connect() as conn:
        warehouse_ids = pd.read_sql(
            text("SELECT DISTINCT warehouse_id FROM silver.shipment_records"), conn
        )["warehouse_id"]

    with engine.begin() as conn:
        for warehouse_id in warehouse_ids:
            conn.execute(
                text("""
                    INSERT INTO gold.dim_warehouse (warehouse_id, region, country_key)
                    VALUES (:warehouse_id, NULL, NULL)
                    ON CONFLICT (warehouse_id) DO NOTHING
                """),
                {"warehouse_id": warehouse_id},
            )
    return len(warehouse_ids)


def populate_fact_shipment(engine: Engine) -> int:
    """
    Populate fact_shipment from silver.shipment_records, joined against
    every dimension to resolve surrogate keys.

    Business/architecture context: this is where FR-03 (delay detection)
    becomes a real, queryable Gold-layer column -- delay_days and
    is_delayed are computed HERE, once, using calculate_delay_days() and
    the config-driven delay_threshold_days (NFR-07: business-tunable,
    not hardcoded).

    Note on the JOIN chain: shipment_records -> purchase_orders (for
    supplier/medicine/quantity/cost) -> supplier_master (for country) --
    reconstructing exactly the ER relationships designed in Phase 2.
    """
    config = load_pipeline_config()
    delay_threshold_days = config["shipment_delay"]["delay_threshold_days"]

    with engine.connect() as conn:
        shipments = pd.read_sql(
            text("""
                SELECT
                    sr.shipment_id, sr.warehouse_id, sr.eta, sr.actual_arrival, sr.status,
                    po.po_id, po.order_date, po.quantity, po.total_cost, po.currency,
                    sup.supplier_id, sup.country,
                    med.medicine_id
                FROM silver.shipment_records sr
                JOIN silver.purchase_orders po ON po.po_id = sr.po_id
                JOIN silver.supplier_master sup ON sup.supplier_id = po.supplier_id
                JOIN silver.medicine_catalogue med ON med.medicine_id = po.medicine_id
            """),
            conn,
        )

        dim_supplier = pd.read_sql(text("SELECT supplier_id, supplier_key FROM gold.dim_supplier"), conn)
        dim_medicine = pd.read_sql(text("SELECT medicine_id, medicine_key FROM gold.dim_medicine"), conn)
        dim_warehouse = pd.read_sql(text("SELECT warehouse_id, warehouse_key FROM gold.dim_warehouse"), conn)
        dim_country = pd.read_sql(text("SELECT country_name, country_key FROM gold.dim_country"), conn)

    supplier_key_map = dict(zip(dim_supplier["supplier_id"], dim_supplier["supplier_key"]))
    medicine_key_map = dict(zip(dim_medicine["medicine_id"], dim_medicine["medicine_key"]))
    warehouse_key_map = dict(zip(dim_warehouse["warehouse_id"], dim_warehouse["warehouse_key"]))
    country_key_map = dict(zip(dim_country["country_name"], dim_country["country_key"]))

    rows_loaded = 0
    with engine.begin() as conn:
        for _, row in shipments.iterrows():
            eta = row["eta"]
            actual_arrival = row["actual_arrival"] if pd.notna(row["actual_arrival"]) else None
            delay_days = calculate_delay_days(eta, actual_arrival)
            is_delayed = delay_days is not None and delay_days > delay_threshold_days

            conn.execute(
                text("""
                    INSERT INTO gold.fact_shipment
                        (shipment_id, supplier_key, medicine_key, warehouse_key, country_key,
                         order_date_key, eta_date_key, actual_arrival_date_key,
                         quantity, total_cost, currency, delay_days, is_delayed, status)
                    VALUES
                        (:shipment_id, :supplier_key, :medicine_key, :warehouse_key, :country_key,
                         :order_date_key, :eta_date_key, :actual_arrival_date_key,
                         :quantity, :total_cost, :currency, :delay_days, :is_delayed, :status)
                    ON CONFLICT (shipment_id) DO UPDATE SET
                        delay_days = EXCLUDED.delay_days,
                        is_delayed = EXCLUDED.is_delayed,
                        status = EXCLUDED.status
                """),
                {
                    "shipment_id": row["shipment_id"],
                    "supplier_key": supplier_key_map[row["supplier_id"]],
                    "medicine_key": medicine_key_map[row["medicine_id"]],
                    "warehouse_key": warehouse_key_map[row["warehouse_id"]],
                    "country_key": country_key_map[row["country"]],
                    "order_date_key": int(row["order_date"].strftime("%Y%m%d")),
                    "eta_date_key": int(eta.strftime("%Y%m%d")),
                    "actual_arrival_date_key": int(actual_arrival.strftime("%Y%m%d")) if actual_arrival else None,
                    "quantity": int(row["quantity"]),
                    "total_cost": float(row["total_cost"]),
                    "currency": row["currency"],
                    "delay_days": delay_days,
                    "is_delayed": is_delayed,
                    "status": row["status"],
                },
            )
            rows_loaded += 1

    return rows_loaded


def run_all_gold_loads() -> None:
    """
    Run every Gold population step in strict dependency order.

    dim_date has no dependencies. dim_country depends on silver data
    only. dim_supplier depends on dim_country. dim_medicine and
    dim_warehouse depend on silver data only. fact_shipment depends on
    ALL FOUR dimension tables being fully populated first.
    """
    setup_logging()
    engine = get_engine()
    logger.info("Starting gold load run")

    date_count = populate_dim_date(engine)
    logger.info("Populated dim_date", extra={"rows": date_count})

    country_count = populate_dim_country(engine)
    logger.info("Populated dim_country", extra={"rows": country_count})

    supplier_count = populate_dim_supplier(engine)
    logger.info("Populated dim_supplier", extra={"rows": supplier_count})

    medicine_count = populate_dim_medicine(engine)
    logger.info("Populated dim_medicine", extra={"rows": medicine_count})

    warehouse_count = populate_dim_warehouse(engine)
    logger.info("Populated dim_warehouse", extra={"rows": warehouse_count})

    fact_count = populate_fact_shipment(engine)
    logger.info("Populated fact_shipment", extra={"rows": fact_count})

    logger.info("Gold load run complete")


if __name__ == "__main__":
    run_all_gold_loads()