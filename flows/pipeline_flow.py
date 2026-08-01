"""
Prefect orchestration for the full Bronze -> Silver -> Gold pipeline.

Purpose:
    Implements FR-10 (unattended daily run) and NFR-02 (automatic retry
    with backoff) from the Phase 2 BRD/NFR design. Wraps the four
    existing, already-tested module entry points as Prefect tasks in a
    single linear flow -- no pipeline LOGIC lives in this file, only
    orchestration (sequencing, retries, logging of task-level outcomes).
    This separation was deliberately set up back in Phase 4: every
    run_all_*() function was written with zero Prefect-specific code
    inside it, specifically so it could be wrapped here without
    modification.

Design pattern:
    One @flow, four @task steps, in a strict linear chain. Each task
    takes the previous task's return value as an argument (even though
    it isn't used) -- this is a deliberate Prefect idiom that forces an
    explicit data dependency, guaranteeing sequential execution rather
    than letting Prefect treat independent-looking tasks as safe to
    parallelize. Silver cannot correctly run before Bronze exists, so
    this ordering is not optional.

IMPORTANT -- import order:
    load_dotenv() MUST run before `import prefect`, below. Prefect reads
    the PREFECT_HOME environment variable (see .env) at import time, to
    decide where to store its local flow-run database. If we let our
    usual load_dotenv() call inside src/utils/db.py handle this (as
    every other module does), it happens too late -- Prefect will have
    already imported and defaulted to the shared, machine-wide
    ~/.prefect location. Setting PREFECT_HOME correctly, before import,
    isolates this project's Prefect state from other Prefect-based
    projects that may exist on the same machine with an incompatible
    internal database schema (see conversation history for the exact
    failure this caused).
"""

import os

from dotenv import load_dotenv

load_dotenv(override=True)

# Deliberately unset PREFECT_API_URL for THIS run mode only.
#
# .env defines PREFECT_API_URL=http://localhost:4200/api as a documented
# placeholder for later in this phase, once we deploy a real, persistent
# Prefect server (Decision 4 in our Phase 7 design: prove the flow logic
# works locally/ephemerally FIRST, add server infrastructure after).
#
# If this variable is present when Prefect starts, it interprets it as
# "a real server already exists at this address -- connect to it" rather
# than "start a temporary local one." Since no server is running yet,
# that produces a connection-refused error instead of Prefect's normal
# ephemeral fallback behavior.
#
# Once we actually deploy a persistent Prefect server later in this
# phase, this os.environ.pop() call will be removed -- at that point we
# WANT this variable to take effect.
os.environ.pop("PREFECT_API_URL", None)

from prefect import flow, task  # noqa: E402 -- must follow load_dotenv(), see note above
from src.extraction.internal_sources import run_all_internal_extractions
from src.loading.load_gold import run_all_gold_loads
from src.loading.load_silver import run_all_silver_loads
from src.utils.generate_sample_data import main as generate_sample_data


# -----------------------------------------------------------------------------
# Tasks -- each wraps ONE existing, already-tested pipeline stage.
# -----------------------------------------------------------------------------
# retries=2, retry_delay_seconds=10: implements NFR-02. Chosen deliberately
# short -- enough to survive a transient blip (a momentary Docker network
# hiccup, a brief connection pool exhaustion) without masking a real,
# persistent bug behind long or endless retry loops. A genuinely broken
# task should fail loudly after ~30 seconds, not hide for minutes.

@task(retries=2, retry_delay_seconds=10, name="generate-sample-data")
def generate_data_task() -> str:
    """
    Generates the five internal source files (data/raw/*.csv, *.json).

    Note: in a REAL production deployment, this task would not exist --
    real source files would already be delivered by upstream systems.
    It exists here only because this is a portfolio/demonstration
    project without access to a real organization's ERP exports (see
    BRD Section 7, Assumptions). Flagging this honestly rather than
    presenting a fake source as if it were a real integration.
    """
    generate_sample_data()
    return "data_generated"


@task(retries=2, retry_delay_seconds=10, name="extract-to-bronze")
def extract_bronze_task(_upstream: str) -> str:
    """Runs FR-01: extracts all five internal sources into Bronze tables."""
    run_all_internal_extractions()
    return "bronze_loaded"


@task(retries=2, retry_delay_seconds=10, name="load-silver")
def load_silver_task(_upstream: str) -> str:
    """Runs Gate 1: validates and loads Bronze rows into Silver, quarantining rejects."""
    run_all_silver_loads()
    return "silver_loaded"


@task(retries=2, retry_delay_seconds=10, name="load-gold")
def load_gold_task(_upstream: str) -> str:
    """Runs Gate 2: populates the Gold star schema from Silver."""
    run_all_gold_loads()
    return "gold_loaded"


# -----------------------------------------------------------------------------
# Flow -- pure orchestration, no business logic.
# -----------------------------------------------------------------------------

@flow(name="supply-chain-daily-pipeline", log_prints=True)
def run_pipeline() -> None:
    """
    Runs the full daily pipeline: generate -> Bronze -> Silver -> Gold.

    This is the single entry point FR-10 (unattended daily run) depends
    on -- once deployed with a schedule (a later step in this phase),
    this function is what actually executes every day with no manual
    intervention required.
    """
    step1 = generate_data_task()
    step2 = extract_bronze_task(step1)
    step3 = load_silver_task(step2)
    load_gold_task(step3)


if __name__ == "__main__":
    run_pipeline()