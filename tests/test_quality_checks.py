"""
Tests for src/quality/checks.py

Purpose:
    Tests the pure calculation logic extracted from the check functions
    -- freshness window comparison, deviation percentage calculation,
    tolerance comparison. The check functions THEMSELVES (e.g.
    check_source_freshness) are not unit tested here, since they run
    schema-qualified Postgres queries (bronze.*, silver.*) that SQLite
    cannot execute -- same limitation and same reasoning as
    tests/test_load_gold.py. These functions were verified end-to-end
    against real Postgres in Phase 8 (see conversation history: a full
    pipeline run producing 15/15 passing checks). A future Docker-
    dependent integration test suite would be the right place to test
    the full check functions directly.
"""

import pytest

from src.quality.checks import (
    calculate_row_count_deviation_pct,
    is_within_deviation_tolerance,
    is_within_freshness_window,
)


# -----------------------------------------------------------------------------
# is_within_freshness_window
# -----------------------------------------------------------------------------

def test_freshness_exactly_at_threshold_is_fresh():
    """
    A source ingested EXACTLY at the threshold boundary should count as
    fresh, not stale -- the check uses <=, not <, deliberately, since
    "exactly on time" shouldn't be penalized the same as "late."
    """
    assert is_within_freshness_window(hours_since_last_ingest=26.0, max_age_hours=26.0) is True


def test_freshness_just_under_threshold_is_fresh():
    assert is_within_freshness_window(hours_since_last_ingest=25.9, max_age_hours=26.0) is True


def test_freshness_just_over_threshold_is_stale():
    assert is_within_freshness_window(hours_since_last_ingest=26.1, max_age_hours=26.0) is False


def test_freshness_very_stale_source_is_stale():
    """A source not ingested in days should clearly fail, not an edge case."""
    assert is_within_freshness_window(hours_since_last_ingest=72.0, max_age_hours=26.0) is False


# -----------------------------------------------------------------------------
# calculate_row_count_deviation_pct
# -----------------------------------------------------------------------------

def test_deviation_pct_no_deviation_is_zero():
    """Today's count exactly matching history should report 0% deviation."""
    assert calculate_row_count_deviation_pct(today_count=200, historical_avg=200.0) == 0.0


def test_deviation_pct_higher_count_is_positive():
    """A count HIGHER than history should still report a positive percentage."""
    result = calculate_row_count_deviation_pct(today_count=250, historical_avg=200.0)
    assert result == pytest.approx(25.0)


def test_deviation_pct_lower_count_is_also_positive():
    """
    A count LOWER than history should ALSO report a positive percentage
    -- this is the specific behavior that matters most for our business
    case: a source silently delivering fewer rows is exactly what
    COMPLETE-01 exists to catch, and abs() ensures a drop isn't
    accidentally reported as a negative (and therefore possibly
    misread as 'fine') number.
    """
    result = calculate_row_count_deviation_pct(today_count=100, historical_avg=200.0)
    assert result == pytest.approx(50.0)


def test_deviation_pct_zero_today_count_is_100_percent():
    """
    A source delivering ZERO rows against a real history should report
    exactly 100% deviation -- this is the exact real-world scenario this
    check exists to catch (a source silently stopping delivery entirely).
    """
    result = calculate_row_count_deviation_pct(today_count=0, historical_avg=150.0)
    assert result == pytest.approx(100.0)


def test_deviation_pct_zero_historical_avg_raises():
    """
    Zero historical average has no meaningful percentage answer --
    calling code must check for this case BEFORE calling this function
    (see check_row_count_deviation's "insufficient history" branch).
    """
    with pytest.raises(ValueError):
        calculate_row_count_deviation_pct(today_count=50, historical_avg=0)


# -----------------------------------------------------------------------------
# is_within_deviation_tolerance
# -----------------------------------------------------------------------------

def test_deviation_at_exact_threshold_is_within_tolerance():
    """Same <= boundary reasoning as the freshness check above."""
    assert is_within_deviation_tolerance(deviation_pct=30.0, threshold_pct=30.0) is True


def test_deviation_under_threshold_is_within_tolerance():
    assert is_within_deviation_tolerance(deviation_pct=15.0, threshold_pct=30.0) is True


def test_deviation_over_threshold_exceeds_tolerance():
    assert is_within_deviation_tolerance(deviation_pct=45.0, threshold_pct=30.0) is False