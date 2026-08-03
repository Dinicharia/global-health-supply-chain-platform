# =============================================================================
# Python image for the ETL pipeline and Prefect worker
# =============================================================================
# Purpose: one shared image used by BOTH the prefect-worker service (which
#          executes our scheduled flow) and any one-off manual pipeline
#          commands (docker compose run). Same codebase, same dependencies
#          -- no reason for two separate images.

# Pinned to 3.11, matching our local pyenv version exactly (see Phase 3
# decision) -- keeps local development and container behavior identical,
# avoiding an entire class of "works on my machine, breaks in Docker" bugs.
FROM python:3.11.9-slim

# Set the working directory inside the container -- all subsequent
# COPY/RUN commands operate relative to this path.
WORKDIR /app

# Copy ONLY requirements.txt first, before the rest of the code. This is
# a deliberate Docker layer-caching optimization: Docker caches each
# instruction as a layer, and only rebuilds layers after the first one
# that changed. Since requirements.txt changes far less often than our
# actual source code, structuring it this way means a code-only change
# doesn't force a full dependency reinstall on every rebuild.
COPY requirements.txt .

# --no-cache-dir: don't keep pip's download cache in the image -- it
# provides zero runtime benefit and only bloats the final image size.
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the application code.
COPY . .

# Default command: start a Prefect worker polling our process pool.
# This is what runs when the container starts with no override -- the
# prefect-worker service in docker-compose.yml uses this default as-is;
# one-off commands (docker compose run python-app python -m ...)
# override it explicitly.
CMD ["prefect", "worker", "start", "--pool", "local-process-pool"]