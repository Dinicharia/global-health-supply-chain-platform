#!/bin/bash
set -e

echo "Running Superset database migrations..."
superset db upgrade

echo "Creating admin user (if not already present)..."
superset fab create-admin \
    --username "${SUPERSET_ADMIN_USERNAME:-admin}" \
    --firstname Admin \
    --lastname User \
    --email admin@example.com \
    --password "${SUPERSET_ADMIN_PASSWORD:-changeme}" \
    || echo "Admin user already exists, continuing."

echo "Initializing Superset (default roles/permissions)..."
superset init

echo "Starting Superset web server..."
exec superset run -h 0.0.0.0 -p 8088 --with-threads --reload --debugger