# Developer Guide

**Audience:** An engineer setting up this project for the first time, or
extending it (new source, new validation rule, new dashboard).
**Prerequisite reading:** `docs/02_architecture.md`, if you haven't
already — this guide assumes you understand the Bronze/Silver/Gold
model and don't need it re-explained here.

---

## 1. First-Time Setup

### 1.1 Prerequisites

- Git, Docker Desktop (WSL2 backend on Windows), Git Bash or equivalent
- Python 3.11.9 (pyenv recommended — `.python-version` pins this exactly)
- Power BI Desktop, only if working on the Power BI report

### 1.2 Clone and enter the project

```bash
git clone <repo-url>
cd global-health-supply-chain-platform
```

### 1.3 Two setup paths

**Path A — Docker only (recommended for running the platform):**

```bash
cp .env.docker.example .env.docker    # edit real values before real use
docker compose up -d --build
```

See `README.md` Quick Start for full details, access URLs, and
verification steps.

**Path B — Local Python (for developing/testing pipeline code directly):**

```bash
cp .env.example .env                  # edit real values
pyenv install 3.11.9                  # if not already installed
pyenv local 3.11.9
python -m venv .venv
source .venv/Scripts/activate         # Windows Git Bash
pip install -r requirements.txt
pytest tests/ -v
```

You'll still need a running Postgres for anything beyond unit tests:

```bash
docker compose up -d postgres
# apply schema, in order:
for f in sql/schemas/*.sql; do
  docker compose exec -T postgres psql -U etl_user -d supply_chain_platform < "$f"
done
```

**Windows Git Bash note:** if your Windows username contains a space,
always quote paths. See README's "Known Local Environment Gotchas" for
this and other environment-specific issues encountered during
development.

---

## 2. Running the Pipeline Manually

**Full pipeline, one command (requires Prefect running — Docker or
local, see README):**
```bash
python -m flows.pipeline_flow
```

**Individual stages, for debugging a single layer:**
```bash
python -m src.utils.generate_sample_data
python -m src.extraction.internal_sources
python -m src.loading.load_silver
python -m src.loading.load_gold
python -m src.quality.run_checks
```

**Quality report for a specific run:**
```bash
python -m src.quality.report <run_id>
```

---

## 3. Coding Standards (established and followed throughout this project)

- **PEP 8**, type hints on every function signature.
- **Every function's docstring explains business logic, not just
  mechanics** — see any function in `src/loading/load_gold.py` for the
  pattern: *what* it does, *why* this approach, *what alternative* was
  considered and rejected.
- **No magic numbers.** Business thresholds (delay days, expiry window,
  deviation tolerance) live in `config/pipeline_config.yaml`, never
  hardcoded — see that file's inline comments for what each value means
  and why it was chosen.
- **Pure logic separated from I/O**, specifically so it's unit-testable
  without a live database. This pattern was retrofitted twice in this
  project (`load_gold.py`'s `build_date_dimension_rows`,
  `checks.py`'s `is_within_freshness_window` and friends) after the
  original mixed versions proved untestable — follow it from the start
  for new code rather than repeating that rework.
- **SQL migrations are numbered and never edited after being applied**
  (`sql/schemas/NN_description.sql`) — the number is the run order,
  visible directly from the filesystem.
- **Every non-obvious decision gets a comment explaining *why*, not
  just what.** If you find yourself writing code with no comment and
  the reasoning isn't completely obvious from context, that's a signal
  to add one — this has been true throughout every module in this
  project, not just a suggestion.

---

## 4. How to Add a New Internal Data Source

Walking through this concretely, since it's the most common realistic
extension:

1. **Generate/obtain sample data** — add a function to
   `src/utils/generate_sample_data.py` if simulated, matching the
   pattern of existing `generate_*` functions (inject realistic defects
   deliberately — see that file's docstrings for why).
2. **Add a Bronze table** — new file `sql/schemas/NN_bronze_<name>.sql`,
   following `01_bronze_supplier_master.sql`'s exact pattern: all
   columns `TEXT`, plus `ingested_at`/`source_file`/`run_id`.
3. **Register the source in config** — add an entry to
   `config/pipeline_config.yaml`'s `internal_sources` list (`name`,
   `file_format`, `path`, `bronze_table`). **No new extraction code is
   needed** — `src/extraction/internal_sources.py`'s generic
   `run_all_internal_extractions()` reads this config and handles any
   registered source automatically. This is the actual, working proof
   of NFR-07 (a new source is a config change, not a code change).
4. **Add a Silver table + validator** — new table in
   `sql/schemas/06_silver_tables.sql` pattern (real types, constraints),
   plus a `validate_<name>_row()` function in
   `src/transformation/validators.py`. Add each new rule to
   `docs/silver_validation_rules.md`'s catalogue with a `VR-NN` ID.
5. **Add a loader function** in `src/loading/load_silver.py`, following
   the existing `load_*` functions' pattern exactly (fetch unprocessed
   rows, validate, insert-or-quarantine, track
   passed/quarantined/skipped_duplicate).
6. **Wire it into `run_all_silver_loads()`'s `loaders` list.**
7. **Add tests** — mirror `tests/test_internal_sources.py`'s pattern:
   test the pure validation logic with plain Python dicts, no live DB
   required.

---

## 5. How to Add a New Data Quality Check

1. Write the pure calculation logic first, as a standalone function
   with no `Engine` parameter — see `is_within_freshness_window()` in
   `src/quality/checks.py` as the template.
2. Wrap it in a `check_*()` function that fetches what it needs from
   the database and returns a `CheckResult` (category, check_id,
   description, status, severity, details).
3. Add it to the `results.append(...)` list in
   `src/quality/run_checks.py`'s `run_all_quality_checks()`.
4. Add unit tests for the pure logic in `tests/test_quality_checks.py`.

---

## 6. How to Add a New Dashboard Chart (Superset)

1. If the chart needs a business calculation not already exposed by an
   existing `gold.vw_*` view, **add it to the view in SQL first**
   (`sql/schemas/10_gold_views.sql`), not as chart-level custom SQL in
   Superset. This keeps business logic in version control and shared
   with Power BI — the entire point of the views layer (see
   Architecture Guide, Section 3.5).
2. Grant `bi_reader` access if you added a new view or table:
   `GRANT SELECT ON gold.<new_view> TO bi_reader;`
3. Register/refresh the dataset in Superset's UI (Datasets → the
   existing dataset, or + Dataset if new).
4. Build the chart, save, add to the relevant dashboard.

---

## 7. Testing

```bash
pytest tests/ -v
```

**What's covered:** pure logic in `internal_sources.py`, `load_gold.py`,
`quality/checks.py` — testable without a live database, using SQLite
in-memory or plain Python where a database is needed at all.

**What's NOT covered by automated tests, and why:** the actual
Postgres-schema-qualified behavior of the loaders (`load_silver.py`,
`load_gold.py`'s database-writing functions), and `run_checks.py`'s
orchestration. These were verified manually, repeatedly, against real
Postgres throughout this project's development (see
`docs/04_operations_guide.md` for the specific verification runs). A
Docker-dependent integration test suite would be the correct way to
formalize this — flagged as a known gap, not silently absent.

---

## 8. Git Conventions

Conventional Commits format: `<type>: <description>`, where `type` is
one of `feat`, `fix`, `docs`, `chore`, `refactor`, `test`. Established
from the very first commit of this repository — check `git log --oneline`
for the full pattern in practice.