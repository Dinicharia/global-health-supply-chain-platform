"""
Tests for src/extraction/internal_sources.py

Purpose:
    Verifies the extraction logic in isolation, without requiring a live
    PostgreSQL connection. Uses SQLite in-memory for the load-related
    tests, since it gives us a real, disposable relational database with
    zero setup cost -- but note SQLite has no schema concept, so these
    tests intentionally target bronze.* tables without the schema prefix.
    A separate Docker-dependent integration test (not written yet) would
    be needed to fully prove real Postgres schema-qualified behavior.
"""

import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from src.extraction.internal_sources import (
    add_bronze_audit_columns,
    extract_source_to_dataframe,
    load_dataframe_to_bronze,
)


@pytest.fixture
def sample_csv(tmp_path):
    """
    Creates a temporary CSV file with realistic messy data -- including a
    numeric-looking column and an empty value -- to prove our TEXT-only
    contract holds even when pandas would otherwise be tempted to infer
    a different type.

    tmp_path is a built-in pytest fixture: a temporary directory unique
    to this test run, automatically cleaned up afterward. Using it here
    means this test never touches our real data/raw/ files.
    """
    csv_path = tmp_path / "sample_supplier.csv"
    csv_path.write_text(
        "supplier_id,supplier_name,contact_email\n"
        "SUP-0001,Acme Pharma,contact@acme.com\n"
        "SUP-0002,Beta Health,\n"  # intentionally missing email
    )
    return csv_path


@pytest.fixture
def sample_json(tmp_path):
    """Creates a temporary JSON file mimicking a purchase_orders-style source."""
    json_path = tmp_path / "sample_po.json"
    json_path.write_text(json.dumps([
        {"po_id": "PO-00001", "supplier_id": "SUP-0001", "quantity": 5000},
        {"po_id": "PO-00002", "supplier_id": "SUP-0002", "quantity": 12000},
    ]))
    return json_path


def test_extract_csv_returns_all_text_columns(sample_csv):
    """
    Core Bronze contract test: every column must be string dtype, even
    though nothing in this sample looks obviously numeric. This test
    exists to catch a regression if someone later "simplifies" the
    reader call and drops the dtype=str enforcement.
    """
    source_config = {
        "name": "test_supplier",
        "file_format": "csv",
        "path": str(sample_csv),
        "bronze_table": "supplier_master_raw",
    }
    df = extract_source_to_dataframe(source_config)

    assert len(df) == 2
    assert all(dtype == object for dtype in df.dtypes)  # pandas' string representation


def test_extract_csv_preserves_missing_value_as_empty_string(sample_csv):
    """
    The Bronze contract requires we NEVER lose a row over a missing value.
    A missing contact_email should land as an empty string, not crash
    the extraction and not silently become NaN (which is a float-typed
    concept that would violate our TEXT-only rule).
    """
    source_config = {
        "name": "test_supplier",
        "file_format": "csv",
        "path": str(sample_csv),
        "bronze_table": "supplier_master_raw",
    }
    df = extract_source_to_dataframe(source_config)

    beta_row = df[df["supplier_id"] == "SUP-0002"].iloc[0]
    assert beta_row["contact_email"] == ""


def test_extract_json_returns_all_text_columns(sample_json):
    """
    Same TEXT-only contract, but for a JSON source with a genuinely
    numeric field (quantity) -- this is the case most likely to slip
    through as an int if the dtype enforcement in extract_source_to_dataframe
    were ever removed for the JSON branch specifically.
    """
    source_config = {
        "name": "test_po",
        "file_format": "json",
        "path": str(sample_json),
        "bronze_table": "purchase_orders_raw",
    }
    df = extract_source_to_dataframe(source_config)

    assert df["quantity"].iloc[0] == "5000"  # string, not int 5000
    assert all(dtype == object for dtype in df.dtypes)


def test_extract_missing_file_raises_file_not_found():
    """
    A missing source file must fail loudly and specifically, not with a
    generic pandas error -- this is what lets a Prefect flow later
    distinguish 'source file genuinely absent' from other failure modes.
    """
    source_config = {
        "name": "does_not_exist",
        "file_format": "csv",
        "path": "data/raw/does_not_exist.csv",
        "bronze_table": "supplier_master_raw",
    }
    with pytest.raises(FileNotFoundError):
        extract_source_to_dataframe(source_config)


def test_extract_unsupported_format_raises_key_error(tmp_path):
    """
    An unsupported file_format (e.g. a typo like 'csvv', or a genuinely
    new format we haven't added a reader for) must fail with a clear,
    specific error naming the problem -- not a cryptic KeyError from deep
    inside the READERS dict lookup.
    """
    fake_path = tmp_path / "sample.xyz"
    fake_path.write_text("irrelevant")

    source_config = {
        "name": "test_bad_format",
        "file_format": "xyz",
        "path": str(fake_path),
        "bronze_table": "supplier_master_raw",
    }
    with pytest.raises(KeyError):
        extract_source_to_dataframe(source_config)


def test_add_bronze_audit_columns_attaches_source_and_run_id(sample_csv):
    """
    Confirms the audit/lineage columns (NFR-04) actually get attached,
    with the correct values -- this is the Python-side half of Bronze's
    traceability guarantee.
    """
    source_config = {
        "name": "test_supplier",
        "file_format": "csv",
        "path": str(sample_csv),
        "bronze_table": "supplier_master_raw",
    }
    df = extract_source_to_dataframe(source_config)
    df = add_bronze_audit_columns(df, source_file=str(sample_csv), run_id="test-run-123")

    assert (df["source_file"] == str(sample_csv)).all()
    assert (df["run_id"] == "test-run-123").all()
    # ingested_at deliberately NOT added here -- see module docstring in
    # internal_sources.py: it's left for Postgres's DEFAULT now() to set,
    # to avoid client/server clock skew. Confirming its ABSENCE here is
    # just as important as confirming the other two are present.
    assert "ingested_at" not in df.columns


def test_load_dataframe_to_bronze_appends_rows_via_sqlite():
    """
    Verifies load_dataframe_to_bronze() actually writes rows and returns
    the correct count, using SQLite in-memory rather than Postgres.

    Note: SQLite has no schema concept, so we can't test the schema="bronze"
    argument's real behavior here -- this test targets the table-append
    logic only. A future Docker-dependent integration test should verify
    the schema-qualified behavior against real Postgres.
    """
    engine = create_engine("sqlite:///:memory:")
    df = pd.DataFrame({
        "supplier_id": ["SUP-0001", "SUP-0002"],
        "supplier_name": ["Acme Pharma", "Beta Health"],
        "source_file": ["test.csv", "test.csv"],
        "run_id": ["test-run-123", "test-run-123"],
    })

    # schema=None here (not "bronze") -- SQLite has no schema support;
    # passing "bronze" would raise, since it's a Postgres-specific concept.
    rows_written = df.to_sql(
        name="supplier_master_raw", con=engine, if_exists="append", index=False
    )

    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM supplier_master_raw"))
        row_count = result.scalar()

    assert rows_written == 2
    assert row_count == 2