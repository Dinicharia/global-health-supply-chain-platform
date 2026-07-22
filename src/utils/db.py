"""
Shared database connection logic.

Purpose:
    Every module that reads or writes PostgreSQL goes through
    get_engine() rather than constructing its own connection string.
    This is the single place that knows how to build a valid connection
    to our database -- if that ever changes (new host, connection
    pooling, SSL requirements), this is the only file that changes.

Design pattern:
    Reads credentials from environment variables (.env), never hardcoded
    -- see Phase 2 Security Strategy (NFR-06). Uses SQLAlchemy's engine
    abstraction rather than raw psycopg2 connections because pandas'
    to_sql()/read_sql() (used throughout our extraction/loading code)
    expects a SQLAlchemy engine, not a raw DBAPI connection.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

# Load .env into the environment once, at import time. Safe to call
# multiple times (python-dotenv is idempotent) if other modules also
# call it defensively.
load_dotenv()


def get_engine() -> Engine:
    """
    Build and return a SQLAlchemy engine for the main (read-write) role.

    Purpose:
        Used by extraction and loading code, which needs write access
        to bronze/silver/gold. Superset/Power BI will connect separately
        using the read-only role (POSTGRES_READONLY_USER) defined in
        .env -- see Phase 2 Security Strategy, least-privilege principle.
        We don't build that engine here because BI tools connect
        directly, not through our Python code.

    Returns:
        A SQLAlchemy Engine, ready for use with pandas.to_sql(),
        pandas.read_sql(), or raw engine.connect() calls.

    Raises:
        KeyError: if a required environment variable is missing --
            we fail loudly here rather than defaulting to a guessed
            value, since a silently-wrong DB connection (e.g. connecting
            to the wrong database) is far worse than a clear startup error.
    """
    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db_name = os.environ["POSTGRES_DB"]
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]

    connection_string = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"
    return create_engine(connection_string)