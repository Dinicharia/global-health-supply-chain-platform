"""
Extracts internal source files (CSV/JSON) and loads them into Bronze tables.

Purpose:
    Implements FR-01 (ingest internal supplier/medicine/PO/shipment/
    warehouse data). One generic function handles every internal source,
    driven entirely by config/pipeline_config.yaml -- adding a new
    internal source means adding a config entry, NOT writing new
    extraction code. This is the concrete implementation of NFR-07.

Design pattern:
    Strategy pattern via config, not inheritance/subclassing. A dict of
    "how to read this format" functions (READERS below) is selected at
    runtime based on the source's declared file_format -- this is
    deliberately simple (a dict lookup) rather than a more "enterprise"
    class hierarchy, because our set of formats (csv, json) is small and
    unlikely to grow in a way that would justify the extra abstraction.
"""

import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from src.utils.config import load_pipeline_config
from src.utils.db import get_engine
from src.utils.logging_setup import get_logger, setup_logging

logger = get_logger("pipeline")

# Maps a config-declared file_format to the pandas reader function that
# handles it. Adding support for a new format (e.g. parquet) later means
# adding ONE line here -- not touching the extraction logic below.
READERS = {
    "csv": pd.read_csv,
    "json": pd.read_json,
}


def extract_source_to_dataframe(source_config: dict) -> pd.DataFrame:
    """
    Read one internal source file into a DataFrame, as raw TEXT-shaped data.

    Business/architecture context:
        Per the Bronze layer contract (see sql/schemas/01_bronze_supplier_master.sql
        header), every column must land as TEXT -- we deliberately do NOT
        let pandas infer types (which it does by default, e.g. turning a
        numeric-looking column into int64). A malformed value that pandas
        can't parse as its inferred type would raise an error and silently
        lose that row -- violating FR-11 (immutable, complete raw capture).

    Args:
        source_config: One entry from config["internal_sources"], e.g.
            {"name": "supplier_master", "file_format": "csv",
             "path": "data/raw/supplier_master.csv",
             "bronze_table": "supplier_master_raw"}

    Returns:
        A DataFrame with every column as string dtype, matching what the
        Bronze tables expect.

    Raises:
        FileNotFoundError: if the source file doesn't exist -- logged
            before raising, so a missing source is visible in pipeline
            logs even if the exception is caught upstream by a retry.
        KeyError: if source_config["file_format"] isn't a supported format
            (i.e. not a key in READERS).
    """
    file_format = source_config["file_format"]
    filepath = Path(source_config["path"])
    source_name = source_config["name"]

    if not filepath.exists():
        logger.error(
            f"Source file not found for '{source_name}'",
            extra={"source": source_name, "path": str(filepath)},
        )
        raise FileNotFoundError(f"Source file not found: {filepath}")

    if file_format not in READERS:
        raise KeyError(
            f"Unsupported file_format '{file_format}' for source '{source_name}'. "
            f"Supported formats: {list(READERS.keys())}"
        )

    reader = READERS[file_format]
    # dtype=str forces every column to string on read -- this is what
    # actually enforces the "everything is TEXT in Bronze" contract at
    # the pandas level, not just in the SQL schema.
    if file_format == "csv":
        df = reader(filepath, dtype=str, keep_default_na=False)
    else:
        df = reader(filepath, dtype=str)
        # read_json's dtype=str is less reliable than read_csv's -- cast
        # explicitly as a safety net so downstream behavior is identical
        # regardless of source format.
        df = df.astype(str)

    logger.info(
        f"Extracted '{source_name}' from file",
        extra={"source": source_name, "rows_extracted": len(df)},
    )
    return df


def add_bronze_audit_columns(df: pd.DataFrame, source_file: str, run_id: str) -> pd.DataFrame:
    """
    Attach the three Bronze audit/lineage columns to an extracted DataFrame.

    Business/architecture context:
        Implements the audit columns defined in every bronze.*_raw table
        (see sql/schemas/01_bronze_supplier_master.sql) -- this is the
        Python-side half of NFR-04 (auditability): every Bronze row must
        be traceable to the exact file and run that produced it.

    Args:
        df: The extracted DataFrame, columns already TEXT-typed.
        source_file: Path to the file this data came from.
        run_id: Identifier for this extraction run, shared across every
            source loaded in the same invocation -- lets us later query
            "show me everything loaded in run X" across all Bronze tables.

    Returns:
        The same DataFrame with ingested_at, source_file, and run_id
        columns appended. ingested_at is left for Postgres to populate
        via its DEFAULT now() -- we do NOT set it in Python, to avoid
        clock-skew between the machine running this script and the
        database server being a source of subtle timestamp bugs.
    """
    df = df.copy()
    df["source_file"] = source_file
    df["run_id"] = run_id
    return df


def load_dataframe_to_bronze(df: pd.DataFrame, bronze_table: str, engine: Engine) -> int:
    """
    Write a DataFrame into a bronze schema table.

    Args:
        df: DataFrame with audit columns already attached.
        bronze_table: Table name within the bronze schema (NOT including
            the schema prefix -- e.g. "supplier_master_raw", not
            "bronze.supplier_master_raw").
        engine: SQLAlchemy engine from get_engine().

    Returns:
        Number of rows written.

    Design decision -- if_exists="append", not "replace":
        Bronze is immutable and additive (FR-11) -- every pipeline run
        ADDS new rows, it never overwrites prior ones. "replace" would
        drop and recreate the table, destroying history. This is the
        single most important line in this function to get right.
    """
    df.to_sql(
        name=bronze_table,
        con=engine,
        schema="bronze",
        if_exists="append",
        index=False,  # our tables use BIGSERIAL bronze_id, not the
                      # DataFrame's pandas index, as the primary key
    )
    return len(df)


def run_all_internal_extractions() -> None:
    """
    Extract and load every internal source declared in
    config/pipeline_config.yaml -- the full FR-01 implementation,
    end to end.

    This is the function a Prefect flow will call directly once we
    reach Phase 7 -- deliberately written with no Prefect-specific code
    in it, so it's independently testable via pytest right now, and
    orchestration gets layered on top later without touching this logic.
    """
    setup_logging()
    config = load_pipeline_config()
    engine = get_engine()

    # One run_id shared across every source in this invocation -- lets us
    # later answer "what did run X actually load, across every table."
    run_id = str(uuid.uuid4())
    logger.info("Starting internal source extraction run", extra={"run_id": run_id})

    for source_config in config["internal_sources"]:
        source_name = source_config["name"]
        bronze_table = source_config["bronze_table"]

        df = extract_source_to_dataframe(source_config)
        df = add_bronze_audit_columns(df, source_file=source_config["path"], run_id=run_id)
        rows_loaded = load_dataframe_to_bronze(df, bronze_table, engine)

        logger.info(
            f"Loaded '{source_name}' into bronze.{bronze_table}",
            extra={
                "source": source_name,
                "bronze_table": bronze_table,
                "rows_loaded": rows_loaded,
                "run_id": run_id,
            },
        )

    logger.info("Internal source extraction run complete", extra={"run_id": run_id})


if __name__ == "__main__":
    run_all_internal_extractions()