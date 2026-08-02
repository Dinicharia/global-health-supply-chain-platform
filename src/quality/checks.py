"""
Data Quality Framework: post-pipeline health checks.

Purpose:
    Implements Phase 8 -- automated checks answering "is the platform
    behaving normally today," distinct from Gate 1's row-level
    validation (src/transformation/validators.py). These checks run
    AFTER a full pipeline run completes, inspecting the resulting state
    of Bronze/Silver/Gold as a whole, not individual rows.

Design pattern:
    Each check is a small, independent function returning a CheckResult.
    A check function's only job is to answer one specific question and
    report PASS/FAIL with supporting detail -- it does not decide what
    to DO about a failure (alerting, blocking the pipeline, etc.), which
    is deliberately out of scope for this phase. This mirrors the same
    separation used in validators.py: check logic stays pure and
    testable, orchestration/side-effects stay in the loader/runner layer.

    Pure calculation logic (freshness window comparison, deviation
    percentage math, tolerance comparison) is factored into standalone
    helper functions with no database access -- same refactor pattern
    applied to src/loading/load_gold.py in Phase 6 -- so this logic is
    unit-testable without a live Postgres connection. See
    tests/test_quality_checks.py.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text

from src.utils.config import load_pipeline_config


@dataclass
class CheckResult:
    """
    Outcome of one data quality check.

    Purpose:
        Mirrors ValidationResult's design (src/transformation/validators.py)
        -- a structured result carrying enough detail to write a
        meaningful quality.check_results row, not just a bare boolean.
    """
    check_category: str
    check_id: str
    description: str
    status: str  # "PASS" or "FAIL"
    severity: str  # "INFO", "WARNING", "CRITICAL"
    details: dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Pure calculation helpers -- no database access, fully unit-testable.
# -----------------------------------------------------------------------------
# Extracted deliberately, mirroring the same refactor applied to
# src/loading/load_gold.py in Phase 6: separating pure decision logic
# from database I/O so it's testable without a live Postgres connection.

def is_within_freshness_window(hours_since_last_ingest: float, max_age_hours: float) -> bool:
    """
    FRESH-01 core logic: is a source's most recent ingestion recent enough?

    Args:
        hours_since_last_ingest: Hours elapsed since the last Bronze ingest.
        max_age_hours: Configured freshness threshold.

    Returns:
        True if within the freshness window (fresh), False if stale.
    """
    return hours_since_last_ingest <= max_age_hours


def calculate_row_count_deviation_pct(today_count: int, historical_avg: float) -> float:
    """
    COMPLETE-01 core logic: percentage deviation of today's count from
    the historical average.

    Args:
        today_count: Row count for the run being evaluated.
        historical_avg: Trailing average row count across other runs.

    Returns:
        Absolute percentage deviation (always non-negative -- direction
        of deviation, over vs under, isn't relevant to this check; both
        a sudden spike and a sudden drop are equally worth flagging).

    Raises:
        ValueError: if historical_avg is zero -- caller must handle the
            "insufficient history" case before calling this (dividing by
            zero has no meaningful business answer here).
    """
    if historical_avg == 0:
        raise ValueError("historical_avg cannot be zero -- caller must check for insufficient history first")
    return abs(today_count - historical_avg) / historical_avg * 100


def is_within_deviation_tolerance(deviation_pct: float, threshold_pct: float) -> bool:
    """COMPLETE-01 core logic: is a deviation percentage within tolerance?"""
    return deviation_pct <= threshold_pct


# -----------------------------------------------------------------------------
# Freshness checks
# -----------------------------------------------------------------------------

def check_source_freshness(engine: Engine, bronze_table: str, source_name: str) -> CheckResult:
    """
    FRESH-01: Confirm this source's most recent Bronze ingestion is
    within the configured freshness window.

    Business context: a source that silently stops delivering data
    would pass every row-level validation rule (VR-01 through VR-10)
    trivially, simply by not producing any rows to validate. This check
    exists specifically to catch that failure mode -- "no new bad data"
    is very different from "no new data at all."
    """
    config = load_pipeline_config()
    max_age_hours = config["data_quality"]["freshness_max_age_hours"]

    query = text(f"""
        SELECT EXTRACT(EPOCH FROM (now() - MAX(ingested_at))) / 3600 AS hours_since_last_ingest
        FROM bronze.{bronze_table}
    """)
    with engine.connect() as conn:
        result = conn.execute(query).scalar()

    if result is None:
        return CheckResult(
            check_category="freshness", check_id="FRESH-01",
            description=f"{source_name} has never been ingested",
            status="FAIL", severity="CRITICAL",
            details={"source": source_name, "bronze_table": bronze_table},
        )

    hours_since_last_ingest = round(float(result), 2)
    is_fresh = is_within_freshness_window(hours_since_last_ingest, max_age_hours)

    return CheckResult(
        check_category="freshness", check_id="FRESH-01",
        description=f"{source_name} ingested within {max_age_hours}h window",
        status="PASS" if is_fresh else "FAIL",
        severity="INFO" if is_fresh else "WARNING",
        details={
            "source": source_name,
            "hours_since_last_ingest": hours_since_last_ingest,
            "threshold_hours": max_age_hours,
        },
    )


# -----------------------------------------------------------------------------
# Completeness checks
# -----------------------------------------------------------------------------

def check_row_count_deviation(engine: Engine, bronze_table: str, source_name: str, target_run_id: str) -> CheckResult:
    """
    COMPLETE-01: Compare a given ingestion run's row count against the
    trailing average for this source, flagging significant deviation.

    Business context: a source delivering far fewer (or far more) rows
    than usual is a real signal, even if every individual row is
    perfectly valid -- e.g. an upstream export job that silently broke
    partway through, or a duplicate file accidentally re-sent.

    Bug fix (see conversation history): this function previously
    received the QUALITY CHECK RUN's own newly-generated run_id, which
    never matches anything in Bronze (Bronze rows are stamped with the
    EXTRACTION run's run_id, a completely different value). The caller
    must pass the actual most recent Bronze ingestion run_id for this
    source -- see get_latest_bronze_run_id() in run_checks.py.

    Args:
        target_run_id: the Bronze ingestion run_id to evaluate (NOT the
            quality check's own run_id -- these are different runs).
    """
    config = load_pipeline_config()
    deviation_threshold_pct = config["data_quality"]["row_count_deviation_threshold_pct"]

    today_query = text(f"""
        SELECT COUNT(*) FROM bronze.{bronze_table} WHERE run_id = :target_run_id
    """)
    # Trailing average: every OTHER ingestion run, over all history we have.
    # A production system would window this (e.g. last 30 days); we use
    # full history since our sample data's short timeline doesn't yet
    # justify a rolling window.
    history_query = text(f"""
        SELECT AVG(run_count) FROM (
            SELECT run_id, COUNT(*) AS run_count
            FROM bronze.{bronze_table}
            WHERE run_id != :target_run_id
            GROUP BY run_id
        ) AS per_run_counts
    """)

    with engine.connect() as conn:
        today_count = conn.execute(today_query, {"target_run_id": target_run_id}).scalar() or 0
        historical_avg = conn.execute(history_query, {"target_run_id": target_run_id}).scalar()

    if historical_avg is None or historical_avg == 0:
        return CheckResult(
            check_category="completeness", check_id="COMPLETE-01",
            description=f"{source_name} row count deviation (insufficient history)",
            status="PASS", severity="INFO",
            details={"source": source_name, "today_count": today_count, "historical_avg": None},
        )

    historical_avg = float(historical_avg)
    deviation_pct = calculate_row_count_deviation_pct(today_count, historical_avg)
    is_within_tolerance = is_within_deviation_tolerance(deviation_pct, deviation_threshold_pct)

    return CheckResult(
        check_category="completeness", check_id="COMPLETE-01",
        description=f"{source_name} row count within {deviation_threshold_pct}% of trailing average",
        status="PASS" if is_within_tolerance else "FAIL",
        severity="INFO" if is_within_tolerance else "WARNING",
        details={
            "source": source_name,
            "today_count": today_count,
            "historical_avg": round(historical_avg, 1),
            "deviation_pct": round(deviation_pct, 1),
            "threshold_pct": deviation_threshold_pct,
        },
    )


# -----------------------------------------------------------------------------
# Uniqueness checks
# -----------------------------------------------------------------------------

def check_silver_uniqueness(engine: Engine, silver_table: str, natural_key_column: str) -> CheckResult:
    """
    UNIQUE-01: Directly query Silver for duplicate natural keys.

    Business context: this is deliberately redundant with the PRIMARY
    KEY constraint already enforced in sql/schemas/06_silver_tables.sql
    -- a duplicate literally cannot exist there today. This check exists
    as defense-in-depth: it verifies the DATABASE'S ACTUAL STATE
    directly, independent of trusting that our Python loader and the
    schema constraint are both still correctly in place. If someone
    later modifies the schema and accidentally drops the constraint,
    this check still catches the resulting problem.
    """
    query = text(f"""
        SELECT {natural_key_column}, COUNT(*) as occurrence_count
        FROM silver.{silver_table}
        GROUP BY {natural_key_column}
        HAVING COUNT(*) > 1
    """)
    with engine.connect() as conn:
        duplicates = conn.execute(query).fetchall()

    is_unique = len(duplicates) == 0

    return CheckResult(
        check_category="uniqueness", check_id="UNIQUE-01",
        description=f"silver.{silver_table}.{natural_key_column} has no duplicate natural keys",
        status="PASS" if is_unique else "FAIL",
        severity="INFO" if is_unique else "CRITICAL",
        details={
            "table": silver_table,
            "duplicate_count": len(duplicates),
            "duplicate_keys": [row[0] for row in duplicates[:10]],  # cap at 10 for readability
        },
    )


# -----------------------------------------------------------------------------
# Consistency checks
# -----------------------------------------------------------------------------

def check_silver_gold_shipment_consistency(engine: Engine) -> CheckResult:
    """
    CONSIST-01: Confirm every shipment that passed Silver validation
    made it into Gold's fact_shipment.

    Business context: verifies the Silver -> Gold join chain (see
    populate_fact_shipment in src/loading/load_gold.py) didn't silently
    drop rows -- e.g. via an unintended INNER JOIN mismatch against a
    dimension. Our existing tests check each layer in isolation; this
    check verifies the relationship BETWEEN layers, which unit tests
    on individual functions can't catch.
    """
    query = text("""
        SELECT
            (SELECT COUNT(*) FROM silver.shipment_records) AS silver_count,
            (SELECT COUNT(*) FROM gold.fact_shipment) AS gold_count
    """)
    with engine.connect() as conn:
        row = conn.execute(query).fetchone()

    silver_count, gold_count = row[0], row[1]
    is_consistent = silver_count == gold_count

    return CheckResult(
        check_category="consistency", check_id="CONSIST-01",
        description="silver.shipment_records count matches gold.fact_shipment count",
        status="PASS" if is_consistent else "FAIL",
        severity="INFO" if is_consistent else "CRITICAL",
        details={
            "silver_shipment_count": silver_count,
            "gold_fact_shipment_count": gold_count,
            "difference": silver_count - gold_count,
        },
    )