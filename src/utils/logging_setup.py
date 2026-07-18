"""
Centralized logging setup for the Global Health Supply Chain Intelligence Platform.

Purpose:
    Every module in this codebase should get its logger through
    `get_logger()` rather than calling `logging.getLogger()` directly.
    This guarantees consistent, structured (JSON) output everywhere,
    per the Phase 2 Logging Strategy decision.

Design pattern:
    This module is intentionally the ONLY place that knows how log
    formatting works. If we ever change log format (e.g. add a new
    field to every log line), this is the single file that changes —
    no other module needs to know or care.
"""

import json
import logging
import logging.config
from datetime import datetime, timezone
from pathlib import Path

import yaml


class JsonFormatter(logging.Formatter):
    """
    Formats every log record as a single-line JSON object.

    Why a custom formatter instead of Python's built-in text format:
        The default `logging` formatter produces human-readable text lines
        (e.g. "INFO:pipeline:Loaded 1200 rows"). That's fine to glance at,
        but it can't be queried or filtered programmatically later (e.g.
        "find every log entry where rows_rejected > 100 in the last week").
        A structured JSON line makes every log entry a queryable record,
        matching the Phase 2 decision that logs are a dataset, not just text.

    Alternative considered: a third-party library (e.g. python-json-logger).
        Rejected here deliberately — our formatting needs are simple enough
        that a ~20-line custom class avoids adding a dependency for
        something this small. If our logging needs grow more complex later
        (e.g. log sampling, complex filtering), revisit this decision.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Convert a LogRecord into a JSON string.

        Args:
            record: The LogRecord Python's logging module constructs
                automatically for every log call (e.g. logger.info(...)).

        Returns:
            A single-line JSON string representing this log entry.
        """
        # Base fields present on every log entry, regardless of caller
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Extra fields: callers can pass structured context like
        #   logger.info("Loaded rows", extra={"rows_processed": 1200})
        # This is how a single log call ends up carrying business context
        # (row counts, source names, run IDs) rather than just free text —
        # this is what makes the pipeline/api log examples from Phase 2
        # actually achievable in practice.
        standard_attrs = set(logging.LogRecord(
            "", 0, "", 0, "", (), None
        ).__dict__.keys())
        for key, value in record.__dict__.items():
            if key not in standard_attrs and key != "message":
                log_entry[key] = value

        # If this log call was for an exception, include the traceback —
        # critical for the "error logs" category from Phase 2; a stack
        # trace with no context is far less useful than one attached to
        # the structured record that triggered it.
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def setup_logging(config_path: str = "config/logging_config.yaml") -> None:
    """
    Load logging_config.yaml and apply it via Python's dictConfig.

    Purpose:
        This is the single entry point every entry-point script (a Prefect
        flow, a pytest conftest, a one-off script) calls once, at startup,
        before doing anything else. It reads OUR config file rather than
        hardcoding handler/formatter setup in Python, matching the Phase 2
        decision that logging behavior should be configurable without a
        code change.

    Args:
        config_path: Path to the logging YAML config, relative to wherever
            the calling script is run from. Defaults to the standard
            project location.

    Raises:
        FileNotFoundError: If the config file doesn't exist — we fail loudly
            here rather than silently falling back to unconfigured logging,
            because silent misconfiguration is worse than a clear startup
            error (better to fail at startup than produce unlogged data
            issues later).
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(
            f"Logging config not found at '{config_path}'. "
            "This must exist before any pipeline code runs."
        )

    # Ensure the log directories referenced in the YAML actually exist —
    # FileHandler does NOT create directories automatically, only the file.
    # We create them here defensively rather than assuming they're always
    # present, since a fresh clone of this repo only has .gitkeep files.
    for log_dir in ["logs/pipeline", "logs/api", "logs/error", "logs/transformation"]:
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    with open(config_file, "r", encoding="utf-8") as f:
        config_dict = yaml.safe_load(f)

    logging.config.dictConfig(config_dict)


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger. Thin wrapper over logging.getLogger, kept here
    so every module imports its logger from ONE place (this file) rather
    than calling `logging.getLogger` directly — makes it trivial to change
    logger-acquisition behavior project-wide later if ever needed.

    Args:
        name: Logger name — by convention, use the module's pipeline stage,
            e.g. "pipeline" or "api", matching the loggers defined in
            logging_config.yaml.

    Returns:
        A configured Logger instance.
    """
    return logging.getLogger(name)