# Production Hardening Guide

## Runtime Configuration

Copy `.env.example` to your deployment secret store and inject values as environment variables. Do not commit real values.

Required production settings:

- `APP_ENV=production`
- `DATABASE_URL=mysql+pymysql://...?...charset=utf8mb4`
- `OIDC_JWKS_URL=https://.../.well-known/jwks.json`
- `OTP_PROVIDER=<non-dev provider>`
- `AUTH_DEV_SECRET` must not equal the development default
- `REPORT_TIMEZONE=Asia/Shanghai` unless operations use a different business day

The API validates these at startup in production. Development keeps permissive defaults for local tests.

## Startup

Local MySQL smoke:

```bash
docker compose up --build
```

Manual API startup:

```bash
uv run alembic upgrade head
uv run python src/seed_data.py
uv run uvicorn order_api:app --host 0.0.0.0 --port 8000
```

MCP servers inherit credentials from the process environment. `.claude/mcp.json` intentionally contains no static `API_KEY`, `AUTH_DEV_SECRET`, `OTP_PROVIDER`, or scoped identity verification token.

## Health And Metrics

- Liveness: `GET /api/health`
- Readiness: `GET /api/ready`
- Prometheus text metrics: `GET /api/metrics`

Readiness reports database, expected migration tables, runtime configuration, and RAG backend configuration separately so deployment checks can distinguish code, database, and configuration failures.

## Database Notes

Alembic migrations are explicit. `0001_initial_schema` creates the initial operational tables, `0002` adds usage analytics, and `0003` adds durable conversation state.

For production MySQL:

- Use `utf8mb4`.
- Run `alembic upgrade head` before serving traffic.
- Keep `scripts/backup_mysql.py` scheduled outside the API process.
- Verify idempotency behavior after any connection pool or transaction isolation change.

## Current Limits

- The deterministic dispatcher is the production fallback. `HybridIntentDispatcher` is an adapter seam for a future LLM/RAG implementation.
- The sequence guard prevents same-process number collisions and relies on database unique constraints as a final backstop. Multi-process deployments should move numbering to a database sequence/counter adapter.
- Webhook/email delivery for analytics reports is not enabled without provider credentials; local Markdown output remains the default.
