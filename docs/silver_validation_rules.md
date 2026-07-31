# Silver Layer — Validation Rule Catalogue

**Status:** Authoritative — every rule ID referenced in `sql/schemas/06_silver_tables.sql`
comments and in `src/transformation/validators.py` must trace back to a row here.

Implements Gate 1 from the Phase 2 Medallion Architecture design: a row only
advances from Bronze to Silver after passing every applicable rule below.
Failing rows are written to `silver.quarantine` with the specific `rule_id`
that rejected them — never silently dropped (FR-11, NFR-04).

| Rule ID | Source | Rule | Type | Catches (from generate_sample_data.py) | Action on failure |
|---|---|---|---|---|---|
| VR-01 | supplier_master | `contact_email` may be empty | Documented exception, not a failure | ~10% blank emails | Passes through, `contact_email = NULL` |
| VR-02 | purchase_orders | `supplier_id` must exist in `silver.supplier_master` | Referential integrity | ~5% orphaned `SUP-9999` references | Quarantined |
| VR-03 | purchase_orders | `quantity`, `total_cost` must parse as valid positive numbers | Type/schema validation | Defensive (not currently planted) | Quarantined |
| VR-04 | shipment_records | `eta` must parse as a valid date | Type/schema validation | Defensive | Quarantined |
| VR-05 | shipment_records | `actual_arrival` may be empty ("in transit") | Documented exception, not a failure | ~3% in-transit rows | Passes through, `actual_arrival = NULL` |
| VR-06 | shipment_records | `actual_arrival`, if present, must be a physically plausible date | Business rule | Defensive | Quarantined |
| VR-07 | warehouse_inventory | `quantity_on_hand` must be >= 0 | Business rule / range validation | ~5% negative quantities | Quarantined |
| VR-08 | warehouse_inventory | `expiry_date` must parse as a valid date | Type/schema validation | Defensive | Quarantined |
| VR-09 | all sources | exact duplicate row (same natural key) | Duplicate detection | Defensive | 2nd+ occurrence quarantined |
| VR-10 | all sources | required natural-key column must not be null/empty | Completeness | Defensive | Quarantined |

## Design principle: "missing" is not automatically "invalid"

VR-01 and VR-05 exist to prevent a common data quality anti-pattern: treating
every null as an error. A supplier without an on-file email is a legitimate
business record. A shipment without an arrival date simply hasn't arrived
yet. Rejecting these would generate quarantine noise and erode trust in the
quality framework — the system would be "crying wolf." Every rule above is
either an explicit business-meaningful exception (VR-01, VR-05) or a genuine
integrity/correctness check — never a blanket null check.

## Adding a new rule

1. Add a row to this table with the next available `VR-NN` ID.
2. Implement the check in `src/transformation/validators.py`.
3. Reference the rule ID in any relevant SQL comment or quarantine record.
4. Add a pytest case proving the rule both fires (on bad data) and does
   NOT fire (on valid data that superficially resembles the bad case).