"""
Smoke tests for src/utils/logging_setup.py

Purpose:
    Prove that our structured logging setup actually produces valid,
    parseable JSON before any other module depends on it. This is the
    first test in the project, and it establishes the pattern: tests/
    mirrors src/ file-for-file, per the Phase 2 folder structure decision.
"""

import json
import logging

from src.utils.logging_setup import JsonFormatter, get_logger, setup_logging


def test_json_formatter_produces_valid_json():
    """
    A log record formatted by JsonFormatter must be valid, parseable JSON —
    if this ever breaks, every downstream log-based monitoring query breaks
    silently, so this is worth testing directly and explicitly.
    """
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="pipeline",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)

    # This will raise and fail the test if the output isn't valid JSON
    parsed = json.loads(formatted)

    assert parsed["message"] == "Test message"
    assert parsed["level"] == "INFO"
    assert "timestamp" in parsed


def test_json_formatter_includes_extra_fields():
    """
    Business context passed via `extra={}` must survive into the JSON
    output — this is the specific mechanism Phase 4's pipeline code will
    depend on to log things like row counts and source names.
    """
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="pipeline",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Loaded rows",
        args=(),
        exc_info=None,
    )
    record.rows_processed = 1200  # simulates what `extra={...}` sets

    parsed = json.loads(formatter.format(record))

    assert parsed["rows_processed"] == 1200


def test_setup_logging_runs_without_error():
    """
    setup_logging() should load config/logging_config.yaml and apply it
    without raising — this is an integration-style smoke test confirming
    the YAML file and the Python code actually agree with each other.
    """
    setup_logging()  # uses default path: config/logging_config.yaml
    logger = get_logger("pipeline")
    logger.info("Smoke test log entry")
    # No assertion needed beyond "this didn't raise" — this test's job is
    # to catch a broken YAML/Python mismatch, not to check log content.