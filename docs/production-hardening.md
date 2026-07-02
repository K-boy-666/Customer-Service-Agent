# Production Hardening Guide

## Runtime Configuration

Copy `.env.example` to your deployment secret store and inject values as environment variables. Do not commit real values.

Required production settings:

- `APP_ENV=production`
- `DATABASE_URL=mysql+pymysql://...?...charset=utf8mb4`
- `OIDC_JWKS_URL=https://.../.well-known/jwks.json`
- `OTP_PROVIDER=<non-dev provider>`
- `AUTH_DEV_SECRET` must not equal the development default (supports `AUTH_DEV_SECRET_FILE` for Docker secrets)
- `REPORT_TIMEZONE=Asia/Shanghai` unless operations use a different business day

MySQL connection pool (env-tunable, ignored for SQLite):

- `DB_POOL_SIZE=10`
- `DB_MAX_OVERFLOW=20`
- `DB_POOL_RECYCLE=3600` (far below MySQL default `wait_timeout=28800`)
- `DB_POOL_TIMEOUT=30`

The API validates these at startup in production. Development keeps permissive defaults for local tests.

## Deployment

### Production compose (with Docker secrets)

```bash
# Create secret files (gitignored)
mkdir -p secrets
echo -n "your-auth-secret-min-32-bytes" > secrets/auth_dev_secret

# Deploy with prod overlay + monitoring
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.monitoring.yml \
  --profile monitoring up -d
```

The `docker-entrypoint.sh` runs `alembic upgrade head` before starting uvicorn. The application lifespan skips `init_db()` and seed in production — schema is managed exclusively by Alembic.

### Development (local SQLite)

```bash
docker compose up --build
# or manually:
uv run alembic upgrade head
uv run python src/seed_data.py
uv run uvicorn order_api:app --host 0.0.0.0 --port 8000
```

MCP servers inherit credentials from the process environment. `.claude/mcp.json` intentionally contains no static `API_KEY`, `AUTH_DEV_SECRET`, `OTP_PROVIDER`, or scoped identity verification token.

## Secret Rotation Runbook

| Secret | Method | Impact |
|--------|--------|--------|
| JWT/OIDC signing key | JWKS dual-key: add new key at IdP → deploy (overlap ≥ token TTL) → remove old key after expiry. This project only consumes JWKS, no restart needed. | Zero downtime |
| AUTH_DEV_SECRET | Deploy new secret → rolling restart all instances → old tokens invalidated. OTP TTL default 10 min. | Brief during restart |
| MySQL password | Create new user `CREATE USER 'cs'@'%' IDENTIFIED BY 'new_pw'` → update compose secret → `docker compose up -d` → verify → `DROP USER 'cs_old'` | Zero downtime |
| Backup encryption | `scripts/backup_mysql.py` produces gz output; store on encrypted volume. Follow backup retention policy. | N/A |

Secrets can be injected via env vars or `_FILE` suffix (Docker secrets). Both are supported; `_FILE` takes precedence when set.

## Health And Metrics

- Liveness: `GET /api/health`
- Readiness: `GET /api/ready`
- Prometheus text metrics: `GET /api/metrics` (includes DB count gauges + request latency histogram)

Readiness reports database, expected migration tables, runtime configuration, and RAG backend configuration separately so deployment checks can distinguish code, database, and configuration failures.

## Monitoring Stack

Enable with `--profile monitoring`:

- **Prometheus** (`:9090`): scrapes `/api/metrics` every 15s
- **Grafana** (`:3000`): auto-provisioned Prometheus datasource + customer service dashboard
- **Alertmanager** (`:9093`): alert routing

Alert rules:

- `ApiDown`: scrape target down for 2 min → critical
- `HighHandoffRate`: handoff rate > 30% for 15 min → warning
- `HighApiLatencyP95`: P95 latency > 2s for 10 min → warning

## Database Notes

Alembic migrations are explicit. `0001_initial_schema` creates the initial operational tables, `0002` adds usage analytics, `0003` adds durable conversation state, `0004` adds sequence counters for multi-process-safe numbering.

For production MySQL:

- Use `utf8mb4`.
- Run `alembic upgrade head` before serving traffic (handled by `docker-entrypoint.sh`).
- Keep `scripts/backup_mysql.py` scheduled outside the API process.
- Number generation uses `MysqlCounterSequencer` with `LAST_INSERT_ID` atomic increment (multi-process safe). SQLite path uses in-process locks (single-process only).

## CI/CD

- **CI** (`.github/workflows/ci.yml`): ruff lint + Python 3.10/3.11/3.12 test matrix + MySQL 8.4 migration smoke (up→down→up→seed→table assert). Runs on push to main and PRs.
- **CD** (`.github/workflows/release.yml`): builds and pushes Docker image to GHCR on main push and version tags. Tags: `latest`, `sha-<short>`, `v<tag>`.

## Current Limits

- The deterministic dispatcher is the production fallback. `HybridIntentDispatcher` is an adapter seam for a future LLM/RAG implementation.
- Existing DB count metrics use gauge semantics labeled as counter (each scrape re-queries DB). The request latency histogram is proper Prometheus histogram. Future: migrate all metrics to `prometheus_client`.
- mypy has 12 pre-existing type errors (non-blocking in CI).
- Webhook/email delivery for analytics reports is not enabled without provider credentials; local Markdown output remains the default.
