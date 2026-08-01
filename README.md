# Global Health Supply Chain Intelligence Platform

A production-grade data platform for ingesting, validating, transforming,
and reporting on global health medicine supply chain data — built as an
enterprise-pattern learning project (Medallion architecture, Prefect
orchestration, PostgreSQL, Apache Superset, Power BI).

**Status:** Under active development — see `docs/` for architecture and
requirements documentation.

Full setup instructions will be added in the Documentation phase.

## Running the Prefect Server (local development)

The Prefect server is a separate, long-running process from the pipeline
itself -- it must be started in its own terminal window and left running.

**Important:** `PREFECT_HOME` must be exported directly in the shell
before starting the server. Unlike our Python scripts (which load it
automatically from `.env` via `python-dotenv`), the `prefect` CLI is
run directly and has no way to read our `.env` file on its own.

```bash
cd "/path/to/global-health-supply-chain-platform"
export PREFECT_HOME="$(pwd)/.prefect"
prefect server start
```

Leave this terminal open. In a separate terminal, confirm it's running:

```bash
curl http://localhost:4200/api/health
```

Should return `true`. The dashboard is available at http://127.0.0.1:4200.

**Why a project-local `PREFECT_HOME`:** isolates this project's Prefect
state from other Prefect-based projects that may exist on the same
machine, which could be running a different, incompatible Prefect
version. See `.env.example` for the corresponding `PREFECT_HOME` setting
used by our Python scripts.