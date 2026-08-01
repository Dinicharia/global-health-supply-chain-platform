"""
Tests for src/loading/load_gold.py

Purpose:
    Tests the pure logic in this module (delay calculation, calendar
    generation) without requiring a live PostgreSQL connection -- same
    philosophy as tests/test_internal_sources.py. The database-writing
    functions (populate_dim_date, populate_fact_shipment, etc.) are NOT
    unit tested here; they were already proven correct end-to-end
    against real Postgres (see conversation history, Phase 6 manual
    run). A future integration test suite, explicitly marked as
    requiring Docker, would be the right place to test those directly.
"""

from datetime import date

from src.loading.load_gold import build_date_dimension_rows, calculate_delay_days


# -----------------------------------------------------------------------------
# calculate_delay_days
# -----------------------------------------------------------------------------

def test_delay_days_on_time_shipment_returns_zero():
    """A shipment arriving exactly on its ETA has zero delay, not None."""
    eta = date(2026, 3, 10)
    actual_arrival = date(2026, 3, 10)
    assert calculate_delay_days(eta, actual_arrival) == 0


def test_delay_days_early_shipment_returns_zero_not_negative():
    """
    An early shipment should report 0, not a negative number -- 'delay'
    is a one-directional business concept; arriving early isn't a
    negative delay, it's just not late. This is what the max(..., 0)
    in the real function is specifically guarding against.
    """
    eta = date(2026, 3, 10)
    actual_arrival = date(2026, 3, 8)  # 2 days early
    assert calculate_delay_days(eta, actual_arrival) == 0


def test_delay_days_late_shipment_returns_positive_count():
    """A shipment arriving after its ETA reports the correct positive day count."""
    eta = date(2026, 3, 10)
    actual_arrival = date(2026, 3, 15)
    assert calculate_delay_days(eta, actual_arrival) == 5


def test_delay_days_in_transit_shipment_returns_none():
    """
    A shipment with no actual_arrival yet must return None, not 0 --
    this is the exact distinction the original Phase 2 docstring called
    out: 0 would falsely imply 'on time' for something still in transit.
    """
    eta = date(2026, 3, 10)
    assert calculate_delay_days(eta, None) is None


# -----------------------------------------------------------------------------
# build_date_dimension_rows
# -----------------------------------------------------------------------------

def test_date_dimension_covers_full_range_inclusive():
    """
    A single-year range (non-leap) should produce exactly 365 rows,
    covering Jan 1 through Dec 31 inclusive on both ends.
    """
    rows = build_date_dimension_rows(2025, 2025)  # 2025 is not a leap year
    assert len(rows) == 365
    assert rows[0]["full_date"] == date(2025, 1, 1)
    assert rows[-1]["full_date"] == date(2025, 12, 31)


def test_date_dimension_handles_leap_year():
    """2024 is a leap year -- this must produce 366 rows, not 365."""
    rows = build_date_dimension_rows(2024, 2024)
    assert len(rows) == 366


def test_date_dimension_date_key_format():
    """date_key must be an integer in YYYYMMDD format, e.g. 20250101."""
    rows = build_date_dimension_rows(2025, 2025)
    assert rows[0]["date_key"] == 20250101
    assert rows[-1]["date_key"] == 20251231


def test_date_dimension_quarter_calculation():
    """
    Spot-check quarter boundaries -- a common off-by-one bug in date
    dimension generation is miscalculating quarter at month boundaries
    (e.g. March vs April, the Q1/Q2 boundary).
    """
    rows = build_date_dimension_rows(2025, 2025)
    by_date = {row["full_date"]: row for row in rows}

    assert by_date[date(2025, 3, 31)]["quarter"] == 1  # last day of Q1
    assert by_date[date(2025, 4, 1)]["quarter"] == 2    # first day of Q2
    assert by_date[date(2025, 12, 31)]["quarter"] == 4  # last day of Q4


def test_date_dimension_weekend_flag():
    """
    Spot-check a known Saturday and a known Monday from the calendar --
    2025-01-04 is a Saturday, 2025-01-06 is a Monday (verified against
    a real calendar, not assumed).
    """
    rows = build_date_dimension_rows(2025, 2025)
    by_date = {row["full_date"]: row for row in rows}

    assert by_date[date(2025, 1, 4)]["is_weekend"] is True   # Saturday
    assert by_date[date(2025, 1, 6)]["is_weekend"] is False  # Monday