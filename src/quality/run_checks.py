"""
Runs the full Data Quality Framework check suite and persists results.

Purpose:
    The orchestration layer for Phase 8 -- calls every check function in
    src/quality/checks.py against the current database state, writes
    each result to quality.check_results, and logs a summary. This is
    the function a Prefect task (added to flows/pipeline_flow.py as a
    fifth stage, after Gold) would call.

Design pattern:
    Deliberately NOT wrapped in a Prefect task yet in this file -- same
    "logic first, orchestration wrapping after" pattern established
    since Phase 4's run_all_internal_extractions(). We prove this runs
    correctly standalone before wiring it into the flow.
"""

import json
import uuid

from sqlalchemy import Engine, text

from src.quality.checks import (
    CheckResult,
    check_row_count_deviation,
    check_silver_gold_shipment_consistency,
    check_silver_uniqueness,
    check_source_freshness,
)
from src.utils.db import get_engine
from src.utils.logging_setup import get_logger, setup_logging

logger = get_logger("pipeline")

# Every internal source, with the metadata each check category needs.
# Defined here (not read from pipeline_config.yaml's internal_sources)
# because that config lacks the natural_key_column info uniqueness
# checks need -- adding it there would mix quality-check metadata into
# a file whose job is describing EXTRACTION sources. Kept separate
# deliberately, same "config holds what it's actually responsible for"
# principle as everywhere else in this project.
SOURCES = [
    {"name": "supplier_master", "bronze_table": "supplier_master_raw",
     "silver_table": "supplier_master", "natural_key": "supplier_id"},
    {"name": "medicine_catalogue", "bronze_table": "medicine_catalogue_raw",
     "silver_table": "medicine_catalogue", "natural_key": "medicine_id"},
    {"name": "purchase_orders", "bronze_table": "purchase_orders_raw",
     "silver_table": "purchase_orders", "natural_key": "po_id"},
    {"name": "shipment_records", "bronze_table": "shipment_records_raw",
     "silver_table": "shipment_records", "natural_key": "shipment_id"},
    {"name": "warehouse_inventory", "bronze_table": "warehouse_inventory_raw",
     "silver_table": "warehouse_inventory", "natural_key": None},  # composite key, see note below
]


def get_latest_bronze_run_id(engine: Engine, bronze_table: str) -> str | None:
    """
    Look up the run_id of the most recent ingestion into a Bronze table.

    Business/architecture context (bug fix, see conversation history):
        Row-count deviation checks need to evaluate a SPECIFIC Bronze
        ingestion run's count against history -- that must be an actual
        run_id that exists in Bronze (stamped there by
        src/extraction/internal_sources.py), never a freshly-generated
        UUID belonging to some other process, like the quality check
        run itself.

    Returns:
        The most recent run_id present in this table, or None if the
        table has never been populated.
    """
    query = text(f"""
        SELECT run_id FROM bronze.{bronze_table}
        ORDER BY ingested_at DESC
        LIMIT 1
    """)
    with engine.connect() as conn:
        return conn.execute(query).scalar()


def write_check_result(engine: Engine, run_id: str, result: CheckResult) -> None:
    """Persist one CheckResult to quality.check_results."""
    insert = text("""
        INSERT INTO quality.check_results
            (run_id, check_category, check_id, description, status, severity, details)
        VALUES
            (:run_id, :check_category, :check_id, :description, :status, :severity, :details)
    """)
    with engine.begin() as conn:
        conn.execute(insert, {
            "run_id": run_id,
            "check_category": result.check_category,
            "check_id": result.check_id,
            "description": result.description,
            "status": result.status,
            "severity": result.severity,
            "details": json.dumps(result.details, default=str),
        })


def run_all_quality_checks() -> str:
    """
    Run every quality check against the current database state.

    Returns:
        The run_id used for THIS quality check run (used to group and
        later query these results together) -- distinct from any
        Bronze ingestion run_id, see get_latest_bronze_run_id().
    """
    setup_logging()
    engine = get_engine()
    quality_run_id = str(uuid.uuid4())
    logger.info("Starting data quality check run", extra={"run_id": quality_run_id})

    results: list[CheckResult] = []

    # Freshness + Completeness: per-source checks.
    for source in SOURCES:
        results.append(check_source_freshness(engine, source["bronze_table"], source["name"]))

        latest_ingestion_run_id = get_latest_bronze_run_id(engine, source["bronze_table"])
        if latest_ingestion_run_id is None:
            results.append(CheckResult(
                check_category="completeness", check_id="COMPLETE-01",
                description=f"{source['name']} row count deviation (no ingestion history)",
                status="FAIL", severity="CRITICAL",
                details={"source": source["name"]},
            ))
        else:
            results.append(check_row_count_deviation(
                engine, source["bronze_table"], source["name"], latest_ingestion_run_id
            ))

    # Uniqueness: per-source, skipping warehouse_inventory (composite key --
    # see sql/schemas/06_silver_tables.sql, PRIMARY KEY (warehouse_id,
    # medicine_id, batch_number). A single-column check doesn't apply;
    # a composite-key version is a reasonable future extension, not
    # built now since we have no evidence it's needed yet.
    for source in SOURCES:
        if source["natural_key"] is not None:
            results.append(check_silver_uniqueness(engine, source["silver_table"], source["natural_key"]))

    # Consistency: cross-layer check, not per-source.
    results.append(check_silver_gold_shipment_consistency(engine))

    # Persist every result, then log a summary.
    fail_count = 0
    critical_fail_count = 0
    for result in results:
        write_check_result(engine, quality_run_id, result)
        if result.status == "FAIL":
            fail_count += 1
            if result.severity == "CRITICAL":
                critical_fail_count += 1
            logger.warning(
                f"Quality check FAILED: {result.description}",
                extra={
                    "check_id": result.check_id, "severity": result.severity,
                    "details": result.details, "run_id": quality_run_id,
                },
            )

    logger.info(
        "Data quality check run complete",
        extra={
            "run_id": quality_run_id, "total_checks": len(results),
            "passed": len(results) - fail_count, "failed": fail_count,
            "critical_failed": critical_fail_count,
        },
    )

    return quality_run_id


if __name__ == "__main__":
    run_all_quality_checks()