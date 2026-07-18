"""
Generates realistic simulated source data for local development and testing.

Purpose:
    This project doesn't have access to a real organization's ERP data
    (see BRD Section 7, Assumptions). Instead, we generate clearly-labeled
    simulated data that mimics real-world messiness: missing values,
    occasional bad formats, and a few intentionally orphaned foreign keys.
    This gives Phase 5's validation logic (Gate 1/Gate 2) real defects to
    catch, rather than testing against artificially perfect data.

Design decision:
    A fixed random seed (see SEED below) makes generation fully
    reproducible -- running this script twice produces byte-identical
    output. This matters for debugging: if a data quality issue shows up
    in a specific row, that row will be in the same place every time
    anyone on the team regenerates the data.
"""

import csv
import json
import random
from datetime import timedelta
from pathlib import Path

from faker import Faker

# Fixed seed: see module docstring -- reproducibility over "true" randomness.
SEED = 42
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

# Output directory -- matches config/pipeline_config.yaml internal_sources paths.
RAW_DATA_DIR = Path("data/raw")

# African countries relevant to the business scenario (Phase 1 BRD).
COUNTRIES = ["Kenya", "Nigeria", "Ghana", "Uganda", "Tanzania", "Rwanda", "Ethiopia"]
PERFORMANCE_TIERS = ["Tier 1", "Tier 2", "Tier 3"]
MEDICINE_CATEGORIES = ["Antimalarial", "Antibiotic", "Vaccine", "Antiretroviral", "Analgesic"]


def generate_supplier_master(n: int = 30) -> list[dict]:
    """
    Generate supplier master records.

    Business context: feeds bronze.supplier_master_raw, ultimately
    dim_supplier in Gold (FR-04: supplier performance scoring).

    Intentional data quality issues injected:
        - ~10% of rows have a missing contact_email (common in real
          supplier master data -- not every contact is complete).
    """
    rows = []
    for i in range(1, n + 1):
        row = {
            "supplier_id": f"SUP-{i:04d}",
            "supplier_name": fake.company(),
            "country": random.choice(COUNTRIES),
            "contact_email": fake.company_email() if random.random() > 0.10 else "",
            "contract_start_date": fake.date_between(start_date="-3y", end_date="-6m").isoformat(),
            "performance_tier": random.choice(PERFORMANCE_TIERS),
        }
        rows.append(row)
    return rows


def generate_medicine_catalogue(n: int = 25) -> list[dict]:
    """
    Generate medicine catalogue records.

    Business context: feeds bronze.medicine_catalogue_raw, ultimately
    dim_medicine in Gold (FR-05: expiry risk).
    """
    rows = []
    for i in range(1, n + 1):
        row = {
            "medicine_id": f"MED-{i:04d}",
            "medicine_name": f"{fake.word().capitalize()}{random.choice(['cillin', 'zole', 'vir', 'mycin'])}",
            "category": random.choice(MEDICINE_CATEGORIES),
            "unit_of_measure": random.choice(["tablet", "vial", "bottle", "dose"]),
            "shelf_life_days": str(random.choice([180, 365, 540, 730])),
        }
        rows.append(row)
    return rows


def generate_purchase_orders(n: int, supplier_ids: list[str], medicine_ids: list[str]) -> list[dict]:
    """
    Generate purchase order records.

    Intentional data quality issues injected:
        - ~5% reference a supplier_id that doesn't exist in the supplier
          master -- a realistic orphaned foreign key, the exact defect
          Gate 2 (Silver -> Gold referential integrity) must catch.
    """
    rows = []
    for i in range(1, n + 1):
        supplier_id = (
            f"SUP-{9999}"  # deliberately invalid ~5% of the time
            if random.random() < 0.05
            else random.choice(supplier_ids)
        )
        rows.append({
            "po_id": f"PO-{i:05d}",
            "supplier_id": supplier_id,
            "medicine_id": random.choice(medicine_ids),
            "order_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
            "quantity": str(random.randint(500, 20000)),
            "total_cost": str(round(random.uniform(2000, 150000), 2)),
            "currency": random.choice(["USD", "KES", "NGN", "GHS"]),
        })
    return rows


def generate_shipment_records(purchase_orders: list[dict], warehouse_ids: list[str]) -> list[dict]:
    """
    Generate shipment records, one per purchase order.

    Business context: this is the table FR-03 (delay detection) depends on.

    Intentional data quality issues injected:
        - ~15% have actual_arrival AFTER eta by a random 1-10 days (real
          delays -- this is what our delay_threshold_days config value
          from Phase 3 will need to catch, not a defect but real signal)
        - ~3% have a missing actual_arrival entirely (shipment still in
          transit -- tests the None-handling logic from our earlier
          calculate_delay_days() function design).
    """
    rows = []
    for i, po in enumerate(purchase_orders, start=1):
        eta = fake.date_between(start_date="today", end_date="+60d")

        if random.random() < 0.03:
            actual_arrival = ""  # still in transit
        elif random.random() < 0.15:
            delay_days = random.randint(1, 10)
            # Bug fix: end_date must be calculated relative to eta itself,
            # not relative to today ("+10d" means 10 days from NOW in
            # Faker's shorthand). Since eta can already be up to 60 days
            # in the future, "+10d" from today could be BEFORE eta,
            # producing an invalid (empty) date range. Using an explicit
            # timedelta anchors the delay window correctly to eta.
            actual_arrival = (eta + timedelta(days=delay_days)).isoformat()
        else:
            actual_arrival = eta.isoformat()  # on time

        rows.append({
            "shipment_id": f"SHP-{i:05d}",
            "po_id": po["po_id"],
            "warehouse_id": random.choice(warehouse_ids),
            "eta": eta.isoformat(),
            "actual_arrival": actual_arrival,
            "transport_company": fake.company(),
            "status": "Delivered" if actual_arrival else "In Transit",
        })
    return rows


def generate_warehouse_inventory(n: int, warehouse_ids: list[str], medicine_ids: list[str]) -> list[dict]:
    """
    Generate warehouse inventory records.

    Business context: feeds FR-05 (expiry risk) and FR-06 (threshold breach).

    Intentional data quality issues injected:
        - ~5% have a negative quantity_on_hand -- an impossible value a
          real system could still produce (e.g. a sync bug), which
          Gate 1's business-rule validation (Phase 5) must catch.
    """
    rows = []
    for i in range(1, n + 1):
        quantity = random.randint(-500, 50) if random.random() < 0.05 else random.randint(50, 15000)
        rows.append({
            "warehouse_id": random.choice(warehouse_ids),
            "medicine_id": random.choice(medicine_ids),
            "quantity_on_hand": str(quantity),
            "expiry_date": fake.date_between(start_date="today", end_date="+2y").isoformat(),
            "batch_number": fake.bothify(text="BATCH-####-??").upper(),
        })
    return rows


def write_csv(rows: list[dict], filepath: Path) -> None:
    """Write a list of dict rows to a CSV file, creating parent dirs if needed."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict], filepath: Path) -> None:
    """Write a list of dict rows to a JSON file, creating parent dirs if needed."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def main() -> None:
    """
    Generate all five internal source files in dependency order.

    Order matters: purchase_orders needs supplier_ids and medicine_ids
    to already exist (to generate realistic, mostly-valid foreign keys),
    and shipment_records needs the purchase_orders themselves.
    """
    warehouse_ids = [f"WH-{i:03d}" for i in range(1, 13)]  # 12 warehouses, no dedicated table yet

    suppliers = generate_supplier_master()
    medicines = generate_medicine_catalogue()
    purchase_orders = generate_purchase_orders(
        n=200,
        supplier_ids=[s["supplier_id"] for s in suppliers],
        medicine_ids=[m["medicine_id"] for m in medicines],
    )
    shipments = generate_shipment_records(purchase_orders, warehouse_ids)
    inventory = generate_warehouse_inventory(
        n=150,
        warehouse_ids=warehouse_ids,
        medicine_ids=[m["medicine_id"] for m in medicines],
    )

    write_csv(suppliers, RAW_DATA_DIR / "supplier_master.csv")
    write_csv(medicines, RAW_DATA_DIR / "medicine_catalogue.csv")
    write_json(purchase_orders, RAW_DATA_DIR / "purchase_orders.json")
    write_json(shipments, RAW_DATA_DIR / "shipment_records.json")
    write_csv(inventory, RAW_DATA_DIR / "warehouse_inventory.csv")

    print(f"Generated {len(suppliers)} suppliers -> {RAW_DATA_DIR / 'supplier_master.csv'}")
    print(f"Generated {len(medicines)} medicines -> {RAW_DATA_DIR / 'medicine_catalogue.csv'}")
    print(f"Generated {len(purchase_orders)} purchase orders -> {RAW_DATA_DIR / 'purchase_orders.json'}")
    print(f"Generated {len(shipments)} shipments -> {RAW_DATA_DIR / 'shipment_records.json'}")
    print(f"Generated {len(inventory)} inventory records -> {RAW_DATA_DIR / 'warehouse_inventory.csv'}")


if __name__ == "__main__":
    main()