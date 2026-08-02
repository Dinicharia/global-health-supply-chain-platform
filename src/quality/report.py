"""
Generates a human-readable Data Quality report from quality.check_results.

Purpose:
    Implements "automated quality reports" from the original project
    brief. Turns raw check_results rows into a plain-text summary an
    engineer or analyst can read directly, without writing SQL.
"""

from sqlalchemy import Engine, text

from src.utils.db import get_engine


def generate_report(engine: Engine, run_id: str) -> str:
    """
    Build a plain-text quality report for one run_id.

    Args:
        engine: Database engine.
        run_id: The specific quality check run to report on -- typically
            the value returned by run_all_quality_checks().

    Returns:
        A formatted multi-line string summarizing pass/fail counts by
        category and severity, plus full detail on every failure.
    """
    query = text("""
        SELECT check_category, check_id, description, status, severity, details, checked_at
        FROM quality.check_results
        WHERE run_id = :run_id
        ORDER BY check_category, check_id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"run_id": run_id}).fetchall()

    if not rows:
        return f"No quality check results found for run_id '{run_id}'."

    total = len(rows)
    passed = sum(1 for r in rows if r.status == "PASS")
    failed = total - passed
    critical = sum(1 for r in rows if r.status == "FAIL" and r.severity == "CRITICAL")
    warning = sum(1 for r in rows if r.status == "FAIL" and r.severity == "WARNING")

    lines = [
        "=" * 70,
        "DATA QUALITY REPORT",
        "=" * 70,
        f"Run ID: {run_id}",
        f"Checked at: {rows[0].checked_at}",
        "",
        f"Total checks: {total}   Passed: {passed}   Failed: {failed}",
        f"  Critical failures: {critical}   Warnings: {warning}",
        "",
    ]

    if failed == 0:
        lines.append("All quality checks passed. No issues detected.")
    else:
        lines.append("-" * 70)
        lines.append("FAILURES (by category)")
        lines.append("-" * 70)
        current_category = None
        for row in rows:
            if row.status != "FAIL":
                continue
            if row.check_category != current_category:
                current_category = row.check_category
                lines.append(f"\n[{current_category.upper()}]")
            lines.append(f"  [{row.severity}] {row.check_id}: {row.description}")
            lines.append(f"      Details: {row.details}")

    lines.append("=" * 70)
    return "\n".join(lines)


def print_report_for_run(run_id: str) -> None:
    """Convenience entry point: fetch and print a report for a given run_id."""
    engine = get_engine()
    print(generate_report(engine, run_id))


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m src.quality.report <run_id>")
        sys.exit(1)

    print_report_for_run(sys.argv[1])