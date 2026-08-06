# Architecture Guide

**Status:** Authoritative reference for the platform's design.
**Audience:** Engineers joining this project, or anyone evaluating its design decisions.

---

## 1. System Overview

The platform ingests global health supply chain data, validates and
transforms it through a Medallion architecture (Bronze → Silver → Gold),
runs automated data quality checks, and serves the results through
Apache Superset and Power BI — orchestrated end-to-end by Prefect, on a
daily schedule, entirely inside Docker.

```
Internal sources (CSV/JSON)          Prefect Server
        │                                  │
        ▼                                  │ schedules/tracks
┌───────────────┐   ┌───────────┐   ┌──────┴──────┐   ┌────────────┐
│ Extraction     │──▶│  Bronze   │──▶│   Silver    │──▶│    Gold    │
│ (Python)       │   │ (raw,     │   │ (validated, │   │ (star      │
│                │   │ immutable)│   │  typed)     │   │  schema)   │
└───────────────┘   └───────────┘   └──────┬──────┘   └─────┬──────┘
                                            │                 │
                                     Data Quality        Superset /
                                     Framework            Power BI
                                     (quality.*)         (bi_reader)
```

All of the above runs as four Docker Compose services: `postgres`,
`prefect-server`, `prefect-worker` (runs our Python code), and
`superset`. See Section 6 for the container topology.

---

## 2. Medallion Architecture

### Why three layers, not one

Each layer makes a specific, narrow guarantee, and later layers depend
on earlier ones having already made theirs:

| Layer | Guarantee | Column typing |
|---|---|---|
| **Bronze** | Complete, immutable, exactly-as-received | Every column is `TEXT` — deliberately. A malformed source value must never cause a row to be rejected here; type/format validation is Silver's job. |
| **Silver** | Every row has passed schema, type, and business-rule validation (Gate 1) | Real types — `INTEGER`, `DATE`, `NUMERIC` |
| **Gold** | Dimensionally modeled, referentially complete, BI-ready (Gate 2) | Real types, star schema |

A record that fails Gate 1 or Gate 2 is never silently dropped — it's
written to `silver.quarantine` with a specific rule ID and reason (see
Section 4). This is the single most important design invariant in the
platform: **every row is accounted for, always.**

### Why Bronze is all-TEXT

If Bronze enforced real types, a single malformed value (e.g. a stray
comma in a numeric field) would make that row fail to *insert at all* —
meaning we'd lose data we were supposed to be preserving. Bronze's job
is unconditional capture; validation happens downstream, where a
rejected row can be quarantined with a reason instead of vanishing.

### Why Silver and Gold are separate, not combined

Silver is normalized (one table per source entity, close to 3NF).
Gold is a denormalized star schema. Combining them would mean every
change to BI/reporting requirements forces a change to the same tables
that hold our validated, audited source-of-truth data. Keeping them
separate means Gold can be entirely rebuilt from Silver at any time —
a real resilience property, not just a modeling preference.

---

## 3. Database Schema

Four PostgreSQL schemas, all in one database (`supply_chain_platform`):
`bronze`, `silver`, `gold`, `quality`. Kept in one database (not
separate databases) for operational simplicity at our scale — one
connection pool, one backup job to manage.

### 3.1 Bronze tables

`supplier_master_raw`, `medicine_catalogue_raw`, `purchase_orders_raw`,
`shipment_records_raw`, `warehouse_inventory_raw`. Every table follows
the same pattern: all source columns as `TEXT`, plus three audit
columns — `ingested_at` (server-side timestamp, never client-side, to
avoid clock-skew bugs), `source_file`, `run_id`.

### 3.2 Silver tables

Mirror the five Bronze sources with real types and enforced constraints
— `CHECK` constraints and `REFERENCES` foreign keys as a second,
independent enforcement layer beneath the Python validation logic
(defense in depth: a bug in one layer doesn't silently corrupt data if
the other layer still catches it). See `docs/silver_validation_rules.md`
for the full VR-01–VR-10 rule catalogue.

`silver.quarantine` is shared across all five sources — one table,
holding the full rejected row as JSONB plus the specific rule that
rejected it, rather than five separate per-source quarantine tables.
The questions asked of quarantine data ("how many rejections today,"
"which rule fires most") are the same regardless of source, so one
table with a `source_name` column serves better than five near-identical
tables unioned together.

### 3.3 Gold tables — Star Schema

```
              dim_supplier   dim_medicine
                    \            /
dim_date ── fact_shipment ── dim_warehouse
                    /
              dim_country
```

**Grain of `fact_shipment`: one row per shipment.** This is the single
most important modeling decision — every measure (`delay_days`,
`quantity`, `total_cost`) only makes sense because the grain is
unambiguous.

**Type 1 (overwrite) SCD for now, not Type 2.** `dim_supplier` and
`dim_medicine` overwrite on change rather than versioning history. A
future upgrade to Type 2 (effective-dated rows) is a reasonable
extension, deliberately deferred until the base model was proven
correct — this is why Gold's surrogate keys use `_key` suffixes
(`supplier_key`) rather than reusing the natural `_id` — `_key` signals
"warehouse-generated, may version later," distinct from the source
system's own identity column.

**`dim_date` is pre-generated, independent of any source data** — a
standard data warehousing pattern letting BI tools do month/quarter/
weekend grouping via a join, instead of every dashboard reimplementing
date math.

**Known, documented gaps** (see Section 7 — these are real, honestly-
flagged limitations, not oversights):
- `dim_warehouse.region` / `country_key` are NULL — no warehouse master
  source exists, only warehouse IDs as they appear in shipment records.
- `dim_country.risk_tier` is NULL — depends on external risk data
  (weather, fuel, port congestion) that was never ingested.
- `fact_shipment.total_cost` is not currency-converted — depends on an
  exchange rate source that was never built.

### 3.4 Quality schema

`quality.check_results` — one row per check per run, with
`check_category`, `check_id`, `status`, `severity`, and JSONB `details`.
Severity (`INFO`/`WARNING`/`CRITICAL`) is tracked even though no
alerting layer consumes it yet — deliberately, to avoid a schema
migration when one is eventually built.

### 3.5 Reporting Views (`gold.vw_*`)

`vw_shipment_details`, `vw_supplier_performance`,
`vw_medicine_expiry_risk` — pre-joined, denormalized views that are the
**single source of business logic for both Superset and Power BI.**
Defining joins once, in version-controlled SQL (`sql/schemas/10_gold_views.sql`),
rather than inside each BI tool's own query builder, guarantees both
tools report identical numbers — verified directly in this project:
Superset and Power BI were confirmed to return matching results for the
same business question.

---

## 4. Data Quality Framework

Two distinct layers, not to be confused with each other:

**Gate 1/Gate 2 (row-level validation)** — lives in
`src/transformation/validators.py`, runs *during* the Bronze→Silver and
Silver→Gold loads. Answers "is this specific row valid?"

**Post-pipeline checks** — lives in `src/quality/checks.py`, runs
*after* a full pipeline run completes. Answers "did the platform, as a
whole, behave normally today?" Four categories:

| Category | Question | Example finding this would catch |
|---|---|---|
| Freshness | Did a source actually get ingested recently? | A source silently stops delivering — passes every row-level rule by having zero rows to validate |
| Completeness | Is today's row count wildly different from normal? | A source delivers 3 rows instead of 200 — every row individually valid, but clearly wrong |
| Uniqueness | Any duplicate natural keys in Silver? | Defense-in-depth re-check of the database's actual state, independent of trusting the loader got it right |
| Consistency | Does Gold's row count reconcile against Silver's? | An unintended `INNER JOIN` silently dropping rows in the Silver→Gold load |

Both layers write their outcomes to durable tables (`silver.quarantine`,
`quality.check_results`) rather than only log lines — a finding that
only exists in scrolled-past console output is effectively invisible.

---

## 5. Orchestration (Prefect)

One `@flow`, five sequential `@task`s: `generate-sample-data` →
`extract-to-bronze` → `load-silver` → `load-gold` → `run-quality-checks`.
Each task wraps an existing, independently-tested module entry point —
no pipeline *logic* lives in `flows/pipeline_flow.py`, only
orchestration (sequencing, retry policy).

**Retries:** `retries=2, retry_delay_seconds=10` per task — enough to
survive a transient blip without masking a persistent bug behind long
retry loops.

**Scheduling:** a registered Deployment (`prefect.yaml` locally,
`prefect.docker.yaml` inside containers — see Section 6.3 for why these
differ) with a daily cron schedule (`0 2 * * *`, UTC), executed by a
Prefect Worker polling a `process`-type work pool.

**Idempotency:** every Silver/Gold loader checks whether a row (by
`bronze_id` or natural key) has already been processed before acting —
re-running the pipeline never double-counts or duplicates.

---

## 6. Docker / Deployment Architecture

### 6.1 Services

| Service | Image | Purpose |
|---|---|---|
| `postgres` | `postgres:16` | All application data + Prefect's and Superset's own metadata (separate databases: `supply_chain_platform`, `prefect`, `superset`) |
| `prefect-server` | `prefecthq/prefect:3-python3.11` (official) | Orchestration API/UI |
| `prefect-worker` | custom (`docker/python.Dockerfile`) | Executes our pipeline code |
| `superset` | custom on `apache/superset:latest` | BI dashboards |

### 6.2 Why Prefect's metadata store is PostgreSQL, not its SQLite default

SQLite allows only one writer at a time. Under continuous operation
(Prefect's background scheduler polling every ~10–20s, concurrently with
a worker polling for work), simultaneous writes collide and fail with
`database is locked`, repeating indefinitely. This was discovered
directly in this project — light local testing never surfaced it, but
always-on Docker operation did within minutes. Fixed by pointing
`PREFECT_API_DATABASE_CONNECTION_URL` at a dedicated `prefect` database
in the same Postgres instance, isolated from our own `bronze/silver/
gold/quality` schemas.

### 6.3 Why two `prefect.yaml` files exist

Prefect's deployment "pull step" bakes in a literal working-directory
path at deploy time. That path is fundamentally different between your
Windows host (`C:\Users\...`) and inside a Linux container (`/app`) —
no single path is valid in both. `prefect.yaml` (host path) and
`prefect.docker.yaml` (`/app`) are deployed with `--prefect-file`,
mirroring the same split already established for environment variables
(`.env` vs `.env.docker`).

### 6.4 Environment variable files

| File | Loaded by | Purpose |
|---|---|---|
| `.env` | Local Python scripts (`python -m ...`), and Compose's own `${VAR}` substitution inside `docker-compose.yml` | Host-machine values (`POSTGRES_HOST=localhost`) |
| `.env.docker` | Containers, via `env_file:` | Container-network values (`POSTGRES_HOST=postgres`) |

Both need Superset-related variables (`SUPERSET_SECRET_KEY`, etc.) —
these are two genuinely separate mechanisms that happen to require the
same values, a real, non-obvious gotcha documented directly in these
files' own comments.

### 6.5 Security: least-privilege BI access

`bi_reader` is a dedicated, password-protected PostgreSQL role with
`SELECT`-only grants on `gold` and `silver` — no access to `bronze`, no
write access anywhere. Both Superset and Power BI connect using this
role, never the main `etl_user` credential. Verified in both directions:
a `SELECT` against `gold` succeeds; an `INSERT` and a `SELECT` against
`bronze` both fail with `permission denied`.

### 6.6 Host port conflicts

This machine runs a native Windows PostgreSQL service that also listens
on port 5432. Our Postgres container is mapped to **host port 5433**
specifically to avoid this collision (internal container-to-container
traffic is unaffected — those connections use the Docker service name
`postgres:5432`, never the published host port). See `README.md`,
"Known Local Environment Gotchas."

---

## 7. Known Gaps (Honest Scope Boundaries)

Documented explicitly, not hidden, consistent with this project's
practice throughout:

- **No real warehouse master data source** — only warehouse IDs as they
  appear in shipment/inventory records. `dim_warehouse.region`/
  `country_key` are NULL.
- **No external data ingestion** (weather, exchange rates, fuel prices,
  port congestion, country risk) — `config/pipeline_config.yaml`'s
  `external_sources` are all configured `use_live_api: false` with no
  simulated fallback built. `dim_country.risk_tier` is NULL;
  `fact_shipment` costs are not FX-converted.
- **SCD Type 1 only** — no historical versioning of dimension changes.
- **Superset dashboard/chart definitions are not version-controlled** —
  they live in Superset's own metadata database, not this repository.
  Superset supports exporting dashboards as YAML for version control; a
  reasonable future addition, not built.
- **`.pbix` file is git-ignored** — Power BI's Import-mode file embeds a
  full data snapshot; the real "source" is the DAX measures (documented
  in this repo's history) and the `gold.vw_*` views.