# Power BI Setup Guide

**Purpose:** Step-by-step setup for connecting Power BI Desktop to this
platform's PostgreSQL database. Power BI Desktop is Windows-only and
runs outside Docker entirely — this guide is separate from the rest of
the platform's (fully containerized) setup for that reason.

**Prerequisite reading:** if something here fails, check
`docs/04_operations_guide.md`'s "Power BI" section first — it covers
the three specific issues actually encountered building this project's
report.

---

## 1. Install the Npgsql Driver

Power BI's PostgreSQL connector depends on a separate .NET driver,
**Npgsql**, not bundled with Power BI itself.

**Use version 4.0.10 specifically** — download from:
`https://github.com/npgsql/npgsql/releases/tag/v4.0.10`

Download the `.msi` asset (not the `.zip` or source links).

**Why this exact, older version:** newer Npgsql releases (4.1+) dropped
support for GAC (Global Assembly Cache) registration, which is the
specific mechanism Power BI's connector relies on to detect the driver.
Installing the latest version is the most common cause of "the
PostgreSQL connector still doesn't appear" reports.

**During installation:** on the Custom Setup screen, explicitly select
**"Npgsql GAC Installation"** for local install — this component is not
always selected by default, and skipping it means Power BI won't detect
the driver even though installation otherwise "succeeds."

**After installing:** fully close Power BI Desktop (not just the
window — confirm it's not still running) and reopen it. Providers are
only scanned at startup.

**Verify:** Home → Get Data → More... → search "PostgreSQL" — you
should see **"PostgreSQL database"** listed.

---

## 2. Connect to the Database

Home → Get Data → **PostgreSQL database**

| Field | Value |
|---|---|
| Server | `localhost:5433` |
| Database | `supply_chain_platform` |
| Data Connectivity mode | **Import** |

**Why port 5433, not 5432:** this machine's native Windows PostgreSQL
service also listens on 5432; our Docker container is deliberately
remapped to avoid the collision. See `README.md`'s "Known Local
Environment Gotchas."

**Why `localhost`, not `postgres`:** Power BI Desktop runs directly on
your Windows host, outside Docker — it reaches the container through
its published host port, not through the internal Docker network
(where the hostname `postgres` would resolve instead).

**Why Import mode, not DirectQuery:** at this project's data volume
(hundreds to low thousands of rows), Import gives faster, more
responsive interaction and full DAX capability, with staleness between
refreshes being a non-issue at this scale. DirectQuery earns its
complexity at genuinely large volumes or real-time freshness
requirements — neither applies here.

Click **OK**.

**If an "Encryption Support" dialog appears:** expected — our local
Postgres has no SSL configured. Click **OK** to proceed with an
unencrypted connection. Fine for local development; would need real TLS
certificates for anything beyond that.

**Credentials:**
- Authentication type: **Database**
- Username: `bi_reader`
- Password: (value of `POSTGRES_READONLY_PASSWORD` in `.env`)

Click **Connect**.

**If authentication fails despite correct credentials:** almost
certainly the port-collision issue again — verify with
`netstat -ano | findstr :5432` and confirm your connection actually used
`5433`. A failed connection to the *wrong* Postgres (the native
Windows one) produces the same generic error as a genuinely wrong
password.

---

## 3. Select Tables

In the Navigator window, check:
- `gold.vw_shipment_details`
- `gold.vw_supplier_performance`
- `gold.vw_medicine_expiry_risk`

Leave individual dimension/fact/silver tables unchecked — the views
already contain everything pre-joined. You should **not** see anything
under a `bronze` schema; if you do, the `bi_reader` role's permissions
need investigating (see Architecture Guide, Section 6.5).

Click **Load** (not Transform Data — no Power Query transformations are
needed; all business logic already lives in the SQL views).

---

## 4. Core DAX Measures

Create these against `'gold vw_shipment_details'` (note: Power BI may
display the table name with the schema prefix retained, space-
separated — always use autocomplete rather than typing table names by
hand, to avoid a naming mismatch).

```dax
Total Shipments = COUNTROWS('gold vw_shipment_details')
```

```dax
On-Time Delivery Rate =
DIVIDE(
    CALCULATE(COUNTROWS('gold vw_shipment_details'), 'gold vw_shipment_details'[is_delayed] = FALSE),
    COUNTROWS('gold vw_shipment_details')
)
```
Format as **Percentage**, 1 decimal place.

```dax
Total Procurement Cost = SUM('gold vw_shipment_details'[total_cost])
```

```dax
Delayed Shipments =
CALCULATE(
    COUNTROWS('gold vw_shipment_details'),
    'gold vw_shipment_details'[is_delayed] = TRUE
)
```

**Why `DIVIDE()` instead of `/`:** `DIVIDE` returns blank instead of
erroring on a zero denominator (e.g. a filtered view with no rows) —
real DAX best practice, not a style preference.

**Why `COUNTROWS(table)` instead of `COUNT(column)`:** counts rows
regardless of nulls in any particular column — the direct DAX
equivalent of SQL's `COUNT(*)`.

---

## 5. Suggested Visuals

- **Card** visuals for `Total Shipments`, `On-Time Delivery Rate`,
  `Total Procurement Cost`
- **Clustered Bar Chart**: `country_name` (axis) × `Delayed Shipments`
  (value) — cross-check against Superset's equivalent chart; both
  should show identical rankings, since both read the same `gold.vw_*`
  views
- **Table**: from `vw_supplier_performance` — `supplier_name`,
  `performance_tier`, `country_name`, `total_shipments`,
  `delayed_shipments`, `delay_rate_pct`, `total_procurement_cost`

---

## 6. Saving the Report

Save as `.pbix` **outside of what git tracks** — this file is
git-ignored (see root `.gitignore`) because Import mode embeds a full
data snapshot inside the binary, making it a generated artifact, not
source code. The real, recoverable "source" of this report is:
- The DAX measures (documented in Section 4 above)
- The `gold.vw_*` views (`sql/schemas/10_gold_views.sql`)
- The visual layout (documented in Section 5 above)

Re-creating the report from these three things is the intended recovery
path if the `.pbix` file is ever lost.