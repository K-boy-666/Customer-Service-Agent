#!/bin/sh
set -e

# Run database migrations before starting the API server.
# In production, this is the sole schema manager — the application lifespan
# does NOT call Base.metadata.create_all or seed dev data.
alembic upgrade head

exec uv run uvicorn order_api:app --host 0.0.0.0 --port 8000
