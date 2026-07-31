"""
Validation and type-conversion logic for Bronze -> Silver (Gate 1).

Purpose:
    Implements the rule catalogue in docs/silver_validation_rules.md
    (VR-01 through VR-10). Each source has a dedicated validate_* function
    that inspects one Bronze row (already extracted as a dict) and returns
    a ValidationResult: either a cleaned, correctly-typed row ready for
    Silver, or a rejection carrying the specific rule_id that fired.

Design pattern:
    Row-by-row functional validation, not a single monolithic "is this
    dataframe valid" check. This keeps each rule independently testable
    (see docs/silver_validation_rules.md, "Adding a new rule" section --
    every rule needs its own passing AND failing test case) and keeps
    the quarantine reason specific rather than "something in this batch
    was wrong."
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class ValidationResult:
    """
    Outcome of validating one row.

    Purpose:
        Carries either a cleaned row (is_valid=True) or a specific
        rejection reason (is_valid=False, rule_id set) -- this is what
        lets the loader write a meaningful quarantine record instead of
        a generic "validation failed" message.
    """
    is_valid: bool
    cleaned_row: dict[str, Any] = field(default_factory=dict)
    rule_id: str | None = None
    rule_description: str | None = None


def safe_parse_date(value: str) -> date | None:
    """
    Attempt to parse a string as an ISO date (YYYY-MM-DD).

    Args:
        value: Raw string from a Bronze TEXT column.

    Returns:
        A date object if parsing succeeds, or None if the string is
        empty/blank (caller decides whether None is acceptable -- see
        VR-05, where a blank actual_arrival is valid) or malformed.

    Design note:
        Returns None on failure rather than raising, because in this
        module a parse failure isn't necessarily fatal to the row -- the
        CALLING validate_* function decides whether a None here means
        "acceptable missing value" or "reject the row." Centralizing
        the try/except here keeps that decision out of low-level parsing.
    """
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def safe_parse_int(value: str) -> int | None:
    """Attempt to parse a string as an integer. Returns None on failure/blank."""
    if not value or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def safe_parse_decimal(value: str) -> float | None:
    """Attempt to parse a string as a decimal number. Returns None on failure/blank."""
    if not value or not value.strip():
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def check_required_fields(row: dict[str, Any], required_fields: list[str]) -> str | None:
    """
    VR-10: confirm every required natural-key/business field is present
    and non-blank.

    Args:
        row: The raw Bronze row (all values still strings).
        required_fields: Column names that must not be null/empty.

    Returns:
        None if all required fields are present, or a human-readable
        description of the first missing field found (fail on the first
        problem rather than collecting all of them -- keeps quarantine
        reasons simple and specific, one row = one primary reason).
    """
    for field_name in required_fields:
        value = row.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"Required field '{field_name}' is missing or blank"
    return None


# -----------------------------------------------------------------------------
# Source-specific validators
# -----------------------------------------------------------------------------

def validate_supplier_master_row(row: dict[str, Any]) -> ValidationResult:
    """
    Validates one bronze.supplier_master_raw row against VR-01 and VR-10.

    Business context: feeds silver.supplier_master, ultimately
    dim_supplier in Gold.
    """
    missing = check_required_fields(
        row, ["supplier_id", "supplier_name", "country", "contract_start_date", "performance_tier"]
    )
    if missing:
        return ValidationResult(is_valid=False, rule_id="VR-10", rule_description=missing)

    contract_start_date = safe_parse_date(row["contract_start_date"])
    if contract_start_date is None:
        return ValidationResult(
            is_valid=False,
            rule_id="VR-10",
            rule_description=f"contract_start_date '{row['contract_start_date']}' is not a valid date",
        )

    # VR-01: contact_email may legitimately be blank -- NOT a rejection.
    contact_email = row.get("contact_email", "").strip() or None

    cleaned = {
        "supplier_id": row["supplier_id"].strip(),
        "supplier_name": row["supplier_name"].strip(),
        "country": row["country"].strip(),
        "contact_email": contact_email,
        "contract_start_date": contract_start_date,
        "performance_tier": row["performance_tier"].strip(),
    }
    return ValidationResult(is_valid=True, cleaned_row=cleaned)


def validate_medicine_catalogue_row(row: dict[str, Any]) -> ValidationResult:
    """Validates one bronze.medicine_catalogue_raw row against VR-10 and shelf_life type check."""
    missing = check_required_fields(
        row, ["medicine_id", "medicine_name", "category", "unit_of_measure", "shelf_life_days"]
    )
    if missing:
        return ValidationResult(is_valid=False, rule_id="VR-10", rule_description=missing)

    shelf_life_days = safe_parse_int(row["shelf_life_days"])
    if shelf_life_days is None or shelf_life_days <= 0:
        return ValidationResult(
            is_valid=False,
            rule_id="VR-03",
            rule_description=f"shelf_life_days '{row['shelf_life_days']}' is not a valid positive integer",
        )

    cleaned = {
        "medicine_id": row["medicine_id"].strip(),
        "medicine_name": row["medicine_name"].strip(),
        "category": row["category"].strip(),
        "unit_of_measure": row["unit_of_measure"].strip(),
        "shelf_life_days": shelf_life_days,
    }
    return ValidationResult(is_valid=True, cleaned_row=cleaned)


def validate_purchase_order_row(row: dict[str, Any], known_supplier_ids: set[str], known_medicine_ids: set[str]) -> ValidationResult:
    """
    Validates one bronze.purchase_orders_raw row against VR-02, VR-03, VR-10.

    Args:
        row: Raw Bronze row.
        known_supplier_ids: Every supplier_id already present in
            silver.supplier_master -- required to enforce VR-02
            (referential integrity) in Python, BEFORE we even attempt
            the insert. Checking here (not just relying on the database
            FK constraint) lets us quarantine with a clear, specific
            reason instead of catching a raw database IntegrityError.
        known_medicine_ids: Same idea, for medicine_id.
    """
    missing = check_required_fields(
        row, ["po_id", "supplier_id", "medicine_id", "order_date", "quantity", "total_cost", "currency"]
    )
    if missing:
        return ValidationResult(is_valid=False, rule_id="VR-10", rule_description=missing)

    # VR-02: referential integrity against supplier_master.
    if row["supplier_id"].strip() not in known_supplier_ids:
        return ValidationResult(
            is_valid=False,
            rule_id="VR-02",
            rule_description=f"supplier_id '{row['supplier_id']}' does not exist in silver.supplier_master",
        )
    if row["medicine_id"].strip() not in known_medicine_ids:
        return ValidationResult(
            is_valid=False,
            rule_id="VR-02",
            rule_description=f"medicine_id '{row['medicine_id']}' does not exist in silver.medicine_catalogue",
        )

    order_date = safe_parse_date(row["order_date"])
    if order_date is None:
        return ValidationResult(
            is_valid=False, rule_id="VR-03",
            rule_description=f"order_date '{row['order_date']}' is not a valid date",
        )

    quantity = safe_parse_int(row["quantity"])
    if quantity is None or quantity <= 0:
        return ValidationResult(
            is_valid=False, rule_id="VR-03",
            rule_description=f"quantity '{row['quantity']}' is not a valid positive integer",
        )

    total_cost = safe_parse_decimal(row["total_cost"])
    if total_cost is None or total_cost < 0:
        return ValidationResult(
            is_valid=False, rule_id="VR-03",
            rule_description=f"total_cost '{row['total_cost']}' is not a valid non-negative number",
        )

    cleaned = {
        "po_id": row["po_id"].strip(),
        "supplier_id": row["supplier_id"].strip(),
        "medicine_id": row["medicine_id"].strip(),
        "order_date": order_date,
        "quantity": quantity,
        "total_cost": total_cost,
        "currency": row["currency"].strip(),
    }
    return ValidationResult(is_valid=True, cleaned_row=cleaned)


def validate_shipment_record_row(row: dict[str, Any], known_po_ids: set[str]) -> ValidationResult:
    """Validates one bronze.shipment_records_raw row against VR-02, VR-04, VR-05, VR-06, VR-10."""
    missing = check_required_fields(
        row, ["shipment_id", "po_id", "warehouse_id", "eta", "transport_company", "status"]
    )
    if missing:
        return ValidationResult(is_valid=False, rule_id="VR-10", rule_description=missing)

    if row["po_id"].strip() not in known_po_ids:
        return ValidationResult(
            is_valid=False, rule_id="VR-02",
            rule_description=f"po_id '{row['po_id']}' does not exist in silver.purchase_orders",
        )

    eta = safe_parse_date(row["eta"])
    if eta is None:
        return ValidationResult(
            is_valid=False, rule_id="VR-04",
            rule_description=f"eta '{row['eta']}' is not a valid date",
        )

    # VR-05: actual_arrival may legitimately be blank -- shipment still in transit.
    raw_arrival = row.get("actual_arrival", "").strip()
    actual_arrival = None
    if raw_arrival:
        actual_arrival = safe_parse_date(raw_arrival)
        if actual_arrival is None:
            return ValidationResult(
                is_valid=False, rule_id="VR-04",
                rule_description=f"actual_arrival '{raw_arrival}' is present but not a valid date",
            )
        # VR-06: a shipment can't arrive before it was even estimated to --
        # a simple, defensible sanity check for physical plausibility.
        if actual_arrival < eta:
            return ValidationResult(
                is_valid=False, rule_id="VR-06",
                rule_description=f"actual_arrival '{actual_arrival}' is before eta '{eta}'",
            )

    cleaned = {
        "shipment_id": row["shipment_id"].strip(),
        "po_id": row["po_id"].strip(),
        "warehouse_id": row["warehouse_id"].strip(),
        "eta": eta,
        "actual_arrival": actual_arrival,
        "transport_company": row["transport_company"].strip(),
        "status": row["status"].strip(),
    }
    return ValidationResult(is_valid=True, cleaned_row=cleaned)


def validate_warehouse_inventory_row(row: dict[str, Any], known_medicine_ids: set[str]) -> ValidationResult:
    """Validates one bronze.warehouse_inventory_raw row against VR-02, VR-07, VR-08, VR-10."""
    missing = check_required_fields(
        row, ["warehouse_id", "medicine_id", "quantity_on_hand", "expiry_date", "batch_number"]
    )
    if missing:
        return ValidationResult(is_valid=False, rule_id="VR-10", rule_description=missing)

    if row["medicine_id"].strip() not in known_medicine_ids:
        return ValidationResult(
            is_valid=False, rule_id="VR-02",
            rule_description=f"medicine_id '{row['medicine_id']}' does not exist in silver.medicine_catalogue",
        )

    quantity_on_hand = safe_parse_int(row["quantity_on_hand"])
    if quantity_on_hand is None:
        return ValidationResult(
            is_valid=False, rule_id="VR-08",
            rule_description=f"quantity_on_hand '{row['quantity_on_hand']}' is not a valid integer",
        )
    # VR-07: negative stock is physically impossible -- the specific
    # defect our Phase 4 generator plants in ~5% of rows.
    if quantity_on_hand < 0:
        return ValidationResult(
            is_valid=False, rule_id="VR-07",
            rule_description=f"quantity_on_hand {quantity_on_hand} is negative",
        )

    expiry_date = safe_parse_date(row["expiry_date"])
    if expiry_date is None:
        return ValidationResult(
            is_valid=False, rule_id="VR-08",
            rule_description=f"expiry_date '{row['expiry_date']}' is not a valid date",
        )

    cleaned = {
        "warehouse_id": row["warehouse_id"].strip(),
        "medicine_id": row["medicine_id"].strip(),
        "batch_number": row["batch_number"].strip(),
        "quantity_on_hand": quantity_on_hand,
        "expiry_date": expiry_date,
    }
    return ValidationResult(is_valid=True, cleaned_row=cleaned)