"""
Loads validated Bronze rows into Silver, quarantining rejects.

Purpose:
    The concrete implementation of Gate 1 (Phase 2 Medallion Architecture).
    For each source, pulls Bronze rows not yet processed, validates each
    via src/transformation/validators.py, and writes the result to either
    the matching silver.* table or silver.quarantine.

Design pattern:
    Incremental/idempotent: a row already present in Silver or quarantine
    (tracked via bronze_id) is skipped on subsequent runs. This means
    re-running this module doesn't reprocess the whole history every
    time -- only genuinely new Bronze rows, which matters for NFR-03
    (batch window performance) as data volume grows over time.
"""

import json

import pandas as pd
from sqlalchemy import Engine, text

from src.transformation.validators import (
    validate_medicine_catalogue_row,
    validate_purchase_order_row,
    validate_shipment_record_row,
    validate_supplier_master_row,
    validate_warehouse_inventory_row,
)
from src.utils.db import get_engine
from src.utils.logging_setup import get_logger, setup_logging

logger = get_logger("pipeline")


def get_unprocessed_bronze_rows(engine: Engine, bronze_table: str, silver_table: str) -> pd.DataFrame:
    """
    Fetch Bronze rows whose bronze_id hasn't yet produced a Silver row
    OR a quarantine record.

    Business/architecture context:
        This is what makes Silver loading incremental (NFR-03) rather
        than reprocessing the entire Bronze history on every run.

    Args:
        engine: Database engine.
        bronze_table: e.g. "supplier_master_raw" (no schema prefix).
        silver_table: e.g. "supplier_master" (no schema prefix) -- used
            only to build the "already processed via success" half of
            the exclusion; quarantine is checked against ALL sources
            uniformly since it's shared.

    Returns:
        DataFrame of unprocessed Bronze rows. Note: unlike extraction's
        dtype=str enforcement, this read does NOT force every column to
        string -- Postgres-typed columns like ingested_at (TIMESTAMPTZ)
        come back as native pandas types (e.g. Timestamp). Any code that
        serializes a full row (see write_to_quarantine) must account for
        this rather than assuming everything is a plain string.
    """
    query = text(f"""
        SELECT b.*
        FROM bronze.{bronze_table} b
        WHERE b.bronze_id NOT IN (
            SELECT bronze_id FROM silver.{silver_table} WHERE bronze_id IS NOT NULL
        )
        AND b.bronze_id NOT IN (
            SELECT bronze_id FROM silver.quarantine
            WHERE target_table = :silver_table AND bronze_id IS NOT NULL
        )
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"silver_table": silver_table})


def write_to_quarantine(
    engine: Engine, source_name: str, target_table: str, row: dict,
    rule_id: str, rule_description: str, bronze_id: int, run_id: str,
) -> None:
    """
    Write one rejected row to silver.quarantine.

    Business/architecture context:
        The concrete implementation of "quarantined, never silently
        dropped" from the Phase 2 Medallion Architecture diagram.

    Bug fix (see conversation history): `row` here comes from a
    pandas DataFrame produced by get_unprocessed_bronze_rows(), which
    does NOT force every column to string the way extraction does.
    Columns like `ingested_at` (Postgres TIMESTAMPTZ) arrive as
    pandas.Timestamp objects, which Python's built-in json module
    cannot serialize on its own -- it raises TypeError.

    Fix: json.dumps(..., default=str) tells json to fall back to
    str() for any object it doesn't natively know how to encode.
    This is deliberately general rather than special-casing
    `ingested_at` specifically -- any future column with a
    non-JSON-native type (e.g. a Decimal, a UUID) is handled the
    same way, without this function needing to know about it.
    """
    insert = text("""
        INSERT INTO silver.quarantine
            (source_name, target_table, rule_id, rule_description, row_data, bronze_id, run_id)
        VALUES
            (:source_name, :target_table, :rule_id, :rule_description, :row_data, :bronze_id, :run_id)
    """)
    with engine.begin() as conn:
        conn.execute(insert, {
            "source_name": source_name,
            "target_table": target_table,
            "rule_id": rule_id,
            "rule_description": rule_description,
            "row_data": json.dumps(row, default=str),
            "bronze_id": bronze_id,
            "run_id": run_id,
        })


def load_supplier_master(engine: Engine, run_id: str) -> tuple[int, int]:
    """
    Process all unprocessed bronze.supplier_master_raw rows.

    Returns:
        (rows_passed, rows_quarantined) -- reported back to the caller
        for logging, matching the structured "rows_processed" pattern
        established in src/extraction/internal_sources.py.
    """
    df = get_unprocessed_bronze_rows(engine, "supplier_master_raw", "supplier_master")
    passed, quarantined = 0, 0

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        result = validate_supplier_master_row(row_dict)

        if result.is_valid:
            insert = text("""
                INSERT INTO silver.supplier_master
                    (supplier_id, supplier_name, country, contact_email,
                     contract_start_date, performance_tier, bronze_id)
                VALUES
                    (:supplier_id, :supplier_name, :country, :contact_email,
                     :contract_start_date, :performance_tier, :bronze_id)
                ON CONFLICT (supplier_id) DO NOTHING
            """)
            with engine.begin() as conn:
                conn.execute(insert, {**result.cleaned_row, "bronze_id": row_dict["bronze_id"]})
            passed += 1
        else:
            write_to_quarantine(
                engine, "supplier_master", "supplier_master", row_dict,
                result.rule_id, result.rule_description, row_dict["bronze_id"], run_id,
            )
            quarantined += 1

    return passed, quarantined


def load_medicine_catalogue(engine: Engine, run_id: str) -> tuple[int, int]:
    """Process all unprocessed bronze.medicine_catalogue_raw rows."""
    df = get_unprocessed_bronze_rows(engine, "medicine_catalogue_raw", "medicine_catalogue")
    passed, quarantined = 0, 0

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        result = validate_medicine_catalogue_row(row_dict)

        if result.is_valid:
            insert = text("""
                INSERT INTO silver.medicine_catalogue
                    (medicine_id, medicine_name, category, unit_of_measure, shelf_life_days, bronze_id)
                VALUES
                    (:medicine_id, :medicine_name, :category, :unit_of_measure, :shelf_life_days, :bronze_id)
                ON CONFLICT (medicine_id) DO NOTHING
            """)
            with engine.begin() as conn:
                conn.execute(insert, {**result.cleaned_row, "bronze_id": row_dict["bronze_id"]})
            passed += 1
        else:
            write_to_quarantine(
                engine, "medicine_catalogue", "medicine_catalogue", row_dict,
                result.rule_id, result.rule_description, row_dict["bronze_id"], run_id,
            )
            quarantined += 1

    return passed, quarantined


def load_purchase_orders(engine: Engine, run_id: str) -> tuple[int, int]:
    """
    Process all unprocessed bronze.purchase_orders_raw rows.

    Note: fetches known_supplier_ids/known_medicine_ids ONCE, upfront,
    rather than querying per-row -- a per-row query here would mean 200+
    round trips to the database for VR-02 checks alone. Fetching once
    and checking against an in-memory set is the correct performance
    pattern for referential checks at this scale.
    """
    df = get_unprocessed_bronze_rows(engine, "purchase_orders_raw", "purchase_orders")
    passed, quarantined = 0, 0

    with engine.connect() as conn:
        known_supplier_ids = set(pd.read_sql(text("SELECT supplier_id FROM silver.supplier_master"), conn)["supplier_id"])
        known_medicine_ids = set(pd.read_sql(text("SELECT medicine_id FROM silver.medicine_catalogue"), conn)["medicine_id"])

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        result = validate_purchase_order_row(row_dict, known_supplier_ids, known_medicine_ids)

        if result.is_valid:
            insert = text("""
                INSERT INTO silver.purchase_orders
                    (po_id, supplier_id, medicine_id, order_date, quantity, total_cost, currency, bronze_id)
                VALUES
                    (:po_id, :supplier_id, :medicine_id, :order_date, :quantity, :total_cost, :currency, :bronze_id)
                ON CONFLICT (po_id) DO NOTHING
            """)
            with engine.begin() as conn:
                conn.execute(insert, {**result.cleaned_row, "bronze_id": row_dict["bronze_id"]})
            passed += 1
        else:
            write_to_quarantine(
                engine, "purchase_orders", "purchase_orders", row_dict,
                result.rule_id, result.rule_description, row_dict["bronze_id"], run_id,
            )
            quarantined += 1

    return passed, quarantined


def load_shipment_records(engine: Engine, run_id: str) -> tuple[int, int]:
    """Process all unprocessed bronze.shipment_records_raw rows."""
    df = get_unprocessed_bronze_rows(engine, "shipment_records_raw", "shipment_records")
    passed, quarantined = 0, 0

    with engine.connect() as conn:
        known_po_ids = set(pd.read_sql(text("SELECT po_id FROM silver.purchase_orders"), conn)["po_id"])

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        result = validate_shipment_record_row(row_dict, known_po_ids)

        if result.is_valid:
            insert = text("""
                INSERT INTO silver.shipment_records
                    (shipment_id, po_id, warehouse_id, eta, actual_arrival,
                     transport_company, status, bronze_id)
                VALUES
                    (:shipment_id, :po_id, :warehouse_id, :eta, :actual_arrival,
                     :transport_company, :status, :bronze_id)
                ON CONFLICT (shipment_id) DO NOTHING
            """)
            with engine.begin() as conn:
                conn.execute(insert, {**result.cleaned_row, "bronze_id": row_dict["bronze_id"]})
            passed += 1
        else:
            write_to_quarantine(
                engine, "shipment_records", "shipment_records", row_dict,
                result.rule_id, result.rule_description, row_dict["bronze_id"], run_id,
            )
            quarantined += 1

    return passed, quarantined


def load_warehouse_inventory(engine: Engine, run_id: str) -> tuple[int, int]:
    """Process all unprocessed bronze.warehouse_inventory_raw rows."""
    df = get_unprocessed_bronze_rows(engine, "warehouse_inventory_raw", "warehouse_inventory")
    passed, quarantined = 0, 0

    with engine.connect() as conn:
        known_medicine_ids = set(pd.read_sql(text("SELECT medicine_id FROM silver.medicine_catalogue"), conn)["medicine_id"])

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        result = validate_warehouse_inventory_row(row_dict, known_medicine_ids)

        if result.is_valid:
            insert = text("""
                INSERT INTO silver.warehouse_inventory
                    (warehouse_id, medicine_id, batch_number, quantity_on_hand, expiry_date, bronze_id)
                VALUES
                    (:warehouse_id, :medicine_id, :batch_number, :quantity_on_hand, :expiry_date, :bronze_id)
                ON CONFLICT (warehouse_id, medicine_id, batch_number) DO NOTHING
            """)
            with engine.begin() as conn:
                conn.execute(insert, {**result.cleaned_row, "bronze_id": row_dict["bronze_id"]})
            passed += 1
        else:
            write_to_quarantine(
                engine, "warehouse_inventory", "warehouse_inventory", row_dict,
                result.rule_id, result.rule_description, row_dict["bronze_id"], run_id,
            )
            quarantined += 1

    return passed, quarantined


def run_all_silver_loads() -> None:
    """
    Run all five Silver loaders in dependency order.

    Order matters, mirroring Phase 4's generation order and the FK
    dependency chain in sql/schemas/06_silver_tables.sql: suppliers and
    medicines have no dependencies; purchase_orders depends on both;
    shipment_records depends on purchase_orders; warehouse_inventory
    depends on medicine_catalogue.
    """
    import uuid

    setup_logging()
    engine = get_engine()
    run_id = str(uuid.uuid4())
    logger.info("Starting silver load run", extra={"run_id": run_id})

    loaders = [
        ("supplier_master", load_supplier_master),
        ("medicine_catalogue", load_medicine_catalogue),
        ("purchase_orders", load_purchase_orders),
        ("shipment_records", load_shipment_records),
        ("warehouse_inventory", load_warehouse_inventory),
    ]

    for name, loader_fn in loaders:
        passed, quarantined = loader_fn(engine, run_id)
        logger.info(
            f"Processed '{name}' into silver",
            extra={
                "source": name, "rows_passed": passed,
                "rows_quarantined": quarantined, "run_id": run_id,
            },
        )

    logger.info("Silver load run complete", extra={"run_id": run_id})


if __name__ == "__main__":
    run_all_silver_loads()