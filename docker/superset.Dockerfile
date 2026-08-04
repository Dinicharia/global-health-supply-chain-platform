# =============================================================================
# Apache Superset image
# =============================================================================
# Purpose: extends the official Superset image with the one thing it's
#          missing out of the box for our use case -- a PostgreSQL
#          driver.

FROM apache/superset:latest

# Bug fix (see conversation history): this image manages its /app/.venv
# environment using `uv`, not standard pip -- there is no pip binary
# inside .venv/bin at all, by design. Superset ships its own official
# helper script, /app/docker/pip-install.sh, which correctly wraps
# `uv pip install` for exactly this situation (installing additional
# packages into their uv-managed venv). Using their own documented
# mechanism rather than reverse-engineering uv's invocation ourselves.
USER root
RUN /app/docker/pip-install.sh --no-cache psycopg2-binary==2.9.10
USER superset