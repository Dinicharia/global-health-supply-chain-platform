"""
Shared pipeline configuration loader.

Purpose:
    Every module that needs a business-tunable value (thresholds, source
    definitions) reads it through load_pipeline_config() rather than
    parsing config/pipeline_config.yaml itself. Centralizes config
    access the same way db.py centralizes database access.
"""

from pathlib import Path

import yaml


def load_pipeline_config(config_path: str = "config/pipeline_config.yaml") -> dict:
    """
    Load and parse the pipeline configuration YAML file.

    Args:
        config_path: Path to the config file, relative to wherever the
            calling script is run from. Defaults to the standard project
            location.

    Returns:
        A dict matching the YAML structure (e.g. config["internal_sources"]
        is a list of source definitions).

    Raises:
        FileNotFoundError: if the config file doesn't exist -- same
            fail-loudly principle as setup_logging() in logging_setup.py.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Pipeline config not found at '{config_path}'. "
            "This must exist before any extraction/transformation code runs."
        )

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)