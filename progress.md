# Progress — 客服智能体 2.0

> Last updated: 2026-07-19
> Active branch: `main`

## Current feature

None active — all planned features are `done` (including cs-profit-engine Task 1–10). See `feature_list.json` for the full inventory.

## Recent completions

| Date | Feature | Commit | Evidence |
|------|---------|--------|----------|
| 2026-07-19 | AI 驱动客服利润引擎 (cs-profit-engine Task 1–10) | — | 9 new modules, 113 new tests, ADR-0011, 8 new feature_list.json entries |
| 2026-07-01 | prometheus_client + pytest-xdist (P5 Phase C) | — | 84 passed, 14 subtests; coverage 76%; 3 new tests; 5x test speedup |
| 2026-07-01 | Structured logging + API rate limiting (P4 Phase B) | — | 81 passed, 14 subtests; coverage 76%; structlog + slowapi; 10 new tests |
| 2026-07-01 | CI & tooling hardening (P3 Phase A) | — | 71 passed, 14 subtests; coverage 75%; 7 improvements; 1 new test |
| 2026-07-01 | Concurrency and load testing (P2) | — | 70 passed, 14 subtests; 3 concurrency bugs fixed; 22 new test cases |
| 2026-07-01 | mypy 12 errors fixed + ci.yml mypy blocking | — | mypy 0 errors; ci.yml continue-on-error removed |
| 2026-06-30 | Production deployment hardening (P1) | — | 52 passed, 14 subtests; CI/CD workflows created; ADR-0006/0007 |
| 2026-06-26 | Merge: customer agent P0/P1 hardening | `d620738` | orchestrator e2e + security tests pass |
| 2026-06-25 | Customer verification + analytics telemetry hardening | `c0badab` | ADR-0005 accepted, tests/test_daily_analytics.py |
| 2026-06-25 | Daily analytics subagent | `c7a72d9` | data-analysis-agent provisioned |
| 2026-06-25 | RAG support + engineering skills | `1b57b65` | tests/test_rag_faq.py passing |
| 2026-06-24 | MCP env config fix + order API bugfixes | `80f7dbf` | MCP servers boot cleanly |
| 2026-06-24 | Production security + data layer | `90ef957` | Security controls tested |
| 2026-06-24 | Multi-agent orchestration system, MCP, tests, memory | `b21b5d0` | Full agent fleet operational |

## Active blockers

_None._

## Planned next

_None — all planned phases (P1–P5) are complete._

## Verification state

```
$ bash init.sh
✔ Python 3.10+ … OK
✔ Dependencies (uv sync) … OK
✔ Database (alembic upgrade head) … OK
✔ order_api :8000 health … OK
✔ MCP server boot smoke … OK
✔ pytest … 45 passed
```

## Agent memory index

- `.claude/agent-memory/customer-service-orchestrator/` — dispatch patterns, de-escalation phrases
- `.claude/agent-memory/customer-service-dispatcher/` — intent patterns, FAQ edge cases
- `memory/` — project-scoped user/feedback/project/reference memories

## Notes

- The CL entrypoint is `customer-service-orchestrator` per ADR-0001. Never bypass.
- MCP servers start automatically via `.claude/mcp.json`. No manual `uvicorn` needed.
- All sub-agents follow ADR-0002 `【客户上下文】+【任务】` → `【处理结果】+【客户回复】+【内部备注】` protocol.
- Tests use SQLite; production targets MySQL via Alembic.

## Risk hardening verification - 2026-06-26

- Cross-platform init entrypoints added: `init.cmd`, `init.ps1`, `init.sh` -> `scripts/harness/init_check.py`.
- Customer-facing MCP path remains `customer-service.handle_customer_message`; `order-server` no longer carries or forwards `IDENTITY_VERIFICATION`.
- Key governance files are ASCII/UTF-8 without BOM, covered by `tests/test_harness_risk_controls.py`.
- Dev JWT default/test secrets are >=32 bytes; pytest warning output is clean.
- Verification evidence:
  - `uv run pytest tests/ -q` -> 37 passed, 14 subtests passed, 0 warnings.
  - `node scripts/harness/validate-harness.mjs` with bundled Node -> weighted total 100/100, all checks passed.
  - `./init.cmd --check-only --skip-tests` -> no failures; warnings only for REST API not running and tests intentionally skipped.
  - `./init.cmd` ran successfully earlier in this session after adding the entrypoint -> tests passed; later rerun was blocked by platform usage limit, not by project failure.

## Agent reliability hardening verification - 2026-06-27

- MCP `handle_customer_message` now classifies only auth/permission failures as `denied`; business/runtime failures return `failed` without claiming that no writes happened.
- Order shipment lookup now treats missing shipment records as a partial business result after order lookup, so prior successful actions in a multi-intent request are still surfaced.
- Orchestrator write fan-out now derives per-operation idempotency keys from the caller key plus operation payload, preventing low-score and complaint tickets from colliding.
- Conversation state now remembers recent `customer_id` and `order_id` by `conversation_id`, allowing follow-up turns such as `I want to return it` to reuse context without storing raw messages.
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\test_orchestrator_e2e.py -q -p no:cacheprovider` -> 7 passed.
  - `.\.venv\Scripts\python.exe -m pytest tests\test_harness_risk_controls.py tests\test_rag_faq.py tests\test_rag_customer_scenarios.py tests\test_security_controls.py -q -p no:cacheprovider` -> 23 passed, 14 subtests passed.
  - `.\init.cmd --check-only --skip-tests` -> no failures; warnings only for REST API not running and tests intentionally skipped.
  - `$env:Path="$env:USERPROFILE\scoop\shims;$env:Path"; rg --version` -> ripgrep 15.1.0.
- Verification caveats:
  - Full `.venv` pytest currently reaches 41 passed, 14 subtests passed, then fails in `DailyAnalyticsTest.test_cli_writes_markdown_report` due Windows temp-directory permission/cleanup errors.
  - `uv run pytest ...` is blocked by Windows permission errors in the uv cache/build temp directories, even with `UV_CACHE_DIR` moved into the workspace.

## Cold-start optimization verification - 2026-06-27

- REST-only FastAPI dependencies were moved out of `security.py` into `src/api_dependencies.py`, so the local Orchestrator tool path no longer imports FastAPI.
- Core runtime/service modules now use Starlette `HTTPException`/status constants while `order_api.py` remains the FastAPI adapter.
- Fast Agent Workflow now documents that one-off customer-message diagnostics should use `orchestrator_mcp_tool.handle_customer_message_tool` or a reused MCP process, not a fresh full `server_customer.py` import.
- Verification evidence:
  - `python -X importtime -c "import orchestrator_mcp_tool"` -> no `fastapi` import in filtered output; `orchestrator_mcp_tool` cumulative import about 0.82s in importtime output.
  - `Measure-Command { python -c "import orchestrator_mcp_tool" }` -> 1.02s cold import; `Measure-Command { python -c "import server_customer" }` -> 3.64s full MCP import.
  - Lightweight complaint probe via `orchestrator_mcp_tool.handle_customer_message_tool` -> 0.0302s handler time, `status=denied`, `error=missing_identity_verification`.
  - Valid verification complaint probe -> `status=needs-human`, `emotional_level=L2`, dispatched complaint/work-order/after-sales/order agents, created 1 ticket.
  - `.\.venv\Scripts\python.exe -m pytest tests\test_orchestrator_e2e.py -q -p no:cacheprovider` -> 7 passed.
  - `.\.venv\Scripts\python.exe -m pytest tests\test_security_controls.py tests\test_harness_risk_controls.py -q -p no:cacheprovider` -> 17 passed.
  - `uv run pytest tests/ -q` -> 42 passed, 14 subtests passed.
  - `.\init.cmd --check-only --skip-tests` -> no failures; warnings only for REST API not running and tests intentionally skipped.
  - `node scripts/harness/validate-harness.mjs` with bundled Node -> weighted total 100/100, all checks passed.

## Production hardening implementation - 2026-06-27

- Removed static MCP credentials from `.claude/mcp.json`; runtime secrets now belong in environment variables documented by `.env.example`.
- Added production config validation (`APP_ENV=production` blocks dev OTP, default dev JWT secret, missing OIDC JWKS, and SQLite production DB).
- Added `/api/ready`, `/api/metrics`, and JSON body write endpoints under `/api/v2/*` while keeping existing query-param endpoints compatible.
- Added a dispatcher module interface with deterministic rule fallback, evidence/fallback metadata, safety notes, and a hybrid adapter seam.
- Added durable `conversation_states` storage plus Alembic migration `0003`, and made `0001_initial_schema` explicit instead of `Base.metadata.create_all`.
- Added handoff packages for human escalation, concurrency-safe in-process number sequencing for ticket/return/survey creation, and Windows-stable daily analytics CLI output tests.
- Added Dockerfile, docker-compose MySQL smoke stack, `.dockerignore`, and `docs/production-hardening.md`.
- Hardened `scripts/harness/init_check.py` on Windows by using UTF-8 subprocess decoding and the existing `.venv` for migrations/tests, avoiding broken uv cache paths.
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider` -> 48 passed, 14 subtests passed.
  - `.\init.cmd` -> all stages pass; one warning only because REST API localhost:8000 is not running.
  - Bundled Node `scripts/harness/validate-harness.mjs` -> weighted total 100/100, all checks passed.
- Verification caveat:
  - Raw `uv run pytest tests/ -q` is still blocked in this Windows/OneDrive environment when uv tries to initialize/build through protected cache/temp paths. The project init now uses `.venv` directly for the test gate.

## Production deployment hardening (P1) - 2026-06-30

- **MySQL migration**: Added dialect-aware number sequencer (`src/numbering.py`) with `InProcessSequencer` (SQLite/tests) and `MysqlCounterSequencer` (MySQL, `LAST_INSERT_ID` atomic increment). Migration `0004_sequence_counters` creates counter table. `service_layer.py` three call sites refactored to use `database.get_number_sequencer()`. Connection pool configured: `pool_recycle=3600`, `pool_size=10`, `max_overflow=20`, `pool_timeout=30`, all env-tunable.
- **Startup migration fix**: `order_api.py` lifespan now skips `init_db()`/seed in production (schema by alembic). Added `docker-entrypoint.sh` (`alembic upgrade head` → uvicorn). Dockerfile updated with ENTRYPOINT + non-root `USER app`.
- **Secrets management**: Added `_read_secret()` in `config.py` supporting `_FILE` suffix (Docker secrets). `security.py` AUTH_DEV_SECRET now uses `_read_secret`. Production compose overlay (`docker-compose.prod.yml`) with Docker secrets. Rotation runbook in ADR-0006.
- **Monitoring**: Added `src/metrics.py` with thread-safe `MetricsRegistry` (request latency histogram). `/api/metrics` now includes `http_request_duration_seconds_bucket/sum/count`. Prometheus + Grafana + Alertmanager stack via `docker-compose.monitoring.yml` with alert rules (ApiDown, HighHandoffRate, HighApiLatencyP95) and Grafana dashboard provisioning.
- **CI/CD**: Added `pyproject.toml` ruff/mypy config. Code cleaned via `ruff check --fix` + `ruff format` (all checks pass). CI workflow (`ci.yml`): lint (ruff + mypy non-blocking) + test matrix (Python 3.10/3.11/3.12) + MySQL 8.4 migration smoke (up→down→up→seed→table assert). CD workflow (`release.yml`): build-push to GHCR.
- ADRs: `0006-secrets-and-deployment.md`, `0007-database-numbering-adapter.md`.
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider` -> 52 passed, 14 subtests passed.
  - `.\.venv\Scripts\ruff.exe check src tests` -> All checks passed.
  - `.\.venv\Scripts\ruff.exe format --check src tests` -> All files formatted.
  - `.\init.cmd --check-only --skip-tests` -> no failures.
  - `node scripts/harness/validate-harness.mjs` -> 100/100, no new warnings.

## prometheus_client + pytest-xdist (P5 Phase C) - 2026-07-01

- **prometheus_client 迁移**: 重写 `src/metrics.py`，用 `prometheus_client` 标准类型替换自定义 `MetricsRegistry`。`Histogram("http_request_duration_seconds", ..., ["route"], buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))` 保留原有指标名和 bucket 边界。5 个 DB-count 指标改为 `Gauge` 类型（反映当前 DB 状态而非单调递增），均设置 `multiprocess_mode='mostrecent'`。保留 `record_request(route, start_time)` 函数签名不变，`request_logging.py` 无需修改。
- **`/api/metrics` 端点改造**: `order_api.py` 的 `/api/metrics` 端点改为返回 `Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)`。DB 查询保留，改为调用 5 个 Gauge 的 `.set()` 方法。多进程检测: `if "PROMETHEUS_MULTIPROC_DIR" in os.environ` → 创建 `CollectorRegistry(support_collectors_without_names=True)` + `MultiProcessCollector` + `generate_latest(registry)`；否则 `generate_latest()` 使用默认 REGISTRY。
- **指标名兼容**: 所有指标名保留原有名称（含 `_total` 后缀），保证 Grafana dashboard/alerts 向后兼容。`prometheus_client.Gauge` 不强制 `_total` 后缀限制（仅 Counter 生效）。
- **pytest-xdist 并行加速**: 添加 `pytest-xdist>=3.5`，使用 `--dist=loadscope` 按 `unittest.TestCase` 类分组。81 测试从 ~63s 降至 ~13s（5x 加速）。负载测试保持串行（`load-test` job 不使用 xdist）。
- **全局状态隔离**: 每个 xdist worker 是独立 OS 进程，`DATABASE_URL`、`_engine`、`_CONVERSATION_STATES`、`_LOCAL_SEQ`、`limiter`、`REGISTRY` 均自动隔离。无需修改任何测试文件或 conftest.py。
- **ADR**: 新增 `docs/adr/0010-prometheus-metrics.md`。
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider -n auto --dist=loadscope -m "not load" --cov --cov-report=term-missing` -> 84 passed, 4 deselected, 14 subtests passed, coverage 76.04%.
  - `.\.venv\Scripts\ruff.exe check src tests` -> All checks passed.
  - `.\.venv\Scripts\mypy.exe src` -> Success: no issues found in 23 source files.
  - `.\init.cmd --check-only --skip-tests` -> no failures.
  - `node scripts/harness/validate-harness.mjs` -> 100/100, no new warnings.

## CI & tooling hardening (P3 Phase A) - 2026-07-01

- **A1 Coverage**: Added `pytest-cov>=5.0` to dev deps. Configured `[tool.coverage.run]` (branch=true, omit seed/server) and `[tool.coverage.report]` (show_missing, fail_under=60). Actual coverage: 75.06%.
- **A2 pip-audit**: Added `pip-audit>=2.7` to dev deps. New CI `audit` job runs `uv run pip-audit` (vulnerabilities cause exit 1; local package warnings non-blocking).
- **A3 Full table assertion**: CI migration-smoke now derives expected tables from `Base.metadata.tables.keys() | {'sequence_counters', 'alembic_version'}` instead of hardcoded 4. Covers all 15 tables (was 4/15 ≈ 27%).
- **A4 Readiness fix**: `/api/ready` now returns HTTP 503 when degraded (was 200). Removed `inspect().get_table_names()` table introspection (migration integrity by CI + alembic). Endpoint no longer takes `Depends(db_session)`, manages session directly for error isolation.
- **A5 OpenAPI docs**: FastAPI app now has title/description/8 openapi_tags. All 32 routes tagged. Production (`APP_ENV=production`) disables `docs_url`/`redoc_url`, keeps `openapi_url`.
- **A6 Exception narrowing**: 3 `except Exception` → `except SQLAlchemyError` in `orchestrator_runtime.py` (analytics recording + conversation state read/persist). 1 `except Exception` → `except (OSError, ImportError, RuntimeError)` in `kb_service.py` (embedding model loading). `database.py:87` and `order_api.py:80` kept as-is (rollback+raise is correct pattern).
- **A7 Load test governance**: CI test job now uses `-m "not load"` (consistent with local). New non-blocking `load-test` job runs `pytest tests/test_load_orchestrator.py -m load` with `continue-on-error: true`.
- **New test**: `test_ready_returns_503_when_db_down` in `test_api_and_migration_e2e.py`.
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider -m "not load" --cov --cov-report=term-missing` -> 71 passed, 4 deselected, 14 subtests passed, coverage 75.06%.
  - `.\.venv\Scripts\ruff.exe check src tests` -> All checks passed.
  - `.\.venv\Scripts\mypy.exe src` -> Success: no issues found in 20 source files.
  - `.\init.cmd --check-only --skip-tests` -> no failures.
  - `node scripts/harness/validate-harness.mjs` -> 100/100, no new warnings.

## Structured logging + API rate limiting (P4 Phase B) - 2026-07-01

- **structlog 配置**: 新增 `src/log_config.py`，使用 ProcessorFormatter 桥接 stdlib logging。shared_processors 链: `merge_contextvars` → `add_logger_name` → `add_log_level` → `PositionalArgumentsFormatter` → `TimeStamper(fmt="iso", utc=True)` → `StackInfoRenderer` → `format_exc_info` → `UnicodeDecoder` → `CallsiteParameterAdder`。生产环境使用 `JSONRenderer`，开发环境使用 `ConsoleRenderer(colors=True)`。`handlers.clear()` 保证多次调用幂等。
- **纯 ASGI 中间件**: 新增 `src/request_logging.py`，`StructuredRequestLoggingMiddleware` 替代原 `metrics_middleware`。使用纯 ASGI（非 BaseHTTPMiddleware）确保 contextvars 跨同步/异步边界可靠传播。绑定 `request_id`/`method`/`path`/`status_code`/`duration_ms` 到 contextvars，并将 `request_id` 写入 `scope["state"]` 供 `request_id_dependency` 使用。
- **stdlib 桥接**: ProcessorFormatter 的 `foreign_pre_chain` 包含 `merge_contextvars`，使 `orchestrator_runtime.py` 等模块中已有的 `logging.getLogger(__name__)` 调用自动获得 request_id 等 contextvars 字段，无需修改业务代码。
- **MCP 日志**: `server_customer.py` 的 `main()` 调用 `configure_logging(log_to_stderr=True)`，日志输出到 stderr，stdout 保留给 JSON-RPC 协议。
- **slowapi 限流**: 新增 `src/rate_limit.py`，`Limiter(key_func=get_remote_address)`。4 层限流: OTP 5/min、orchestrator 60/min、write 30/min、read 120/min。29 个路由添加 `@limiter.limit()` 装饰器及 `request: Request` 参数。3 个健康端点（`/api/health`、`/api/ready`、`/api/metrics`）排除限流。
- **env-driven 配置**: `config.py` 的 `RuntimeConfig` 新增 7 个字段（`log_json`、`rate_limit_enabled`、`rate_limit_storage_uri`、`rate_limit_otp`、`rate_limit_orchestrator`、`rate_limit_write`、`rate_limit_read`），均通过环境变量配置。
- **测试隔离**: `conftest.py` 设置 `RATE_LIMIT_ENABLED=false` 禁用限流。`RateLimitTest` 在 `setUp`/`tearDown` 中直接操作 `limiter.enabled` 和 `limiter.reset()` 实现测试级隔离。
- **权衡**: 移除 `headers_enabled=True`（slowapi 的 SlowAPIMiddleware 基于 BaseHTTPMiddleware，与纯 ASGI 中间件不兼容）。限流功能不受影响，仅不输出 `X-RateLimit-*` 响应头。
- **ADR**: 新增 `docs/adr/0008-structured-logging.md` 和 `docs/adr/0009-rate-limiting.md`。
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider -m "not load" --cov --cov-report=term-missing` -> 81 passed, 4 deselected, 14 subtests passed, coverage 76.30%.
  - `.\.venv\Scripts\ruff.exe check src tests` -> All checks passed.
  - `.\.venv\Scripts\mypy.exe src` -> Success: no issues found in 23 source files.
  - `.\init.cmd --check-only --skip-tests` -> no failures.
  - `node scripts/harness/validate-harness.mjs` -> 100/100, no new warnings.

## Concurrency and load testing (P2) - 2026-07-01

- **Bug fix A — _CONVERSATION_STATES RLock**: Added `_STATES_LOCK = threading.RLock()` to `orchestrator_runtime.py`. Refactored `_get_conversation_state` with double-checked locking (DB I/O outside lock). Refactored `_remember_conversation_state` to wrap read-modify-write + LRU eviction in lock. Added `reset_conversation_states_for_tests()`.
- **Bug fix B — SQLite WAL**: Added `event.listens_for(engine, "connect")` in `database.py` to set `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL` on every new SQLite connection. MySQL branch unaffected.
- **Bug fix C — Idempotency TOCTOU**: `run_idempotent` in `security.py` now catches `IntegrityError` on concurrent insert, rolls back, and returns the cached response from the winning thread. Business writes in the same session are correctly rolled back.
- **Test infrastructure**: Added `tests/conftest.py` with shared fixtures. Registered `slow` and `load` pytest markers in `pyproject.toml`.
- **22 new test cases**: 5 isolation + 5 numbering + 4 idempotency + 4 WAL + 4 load (mark=load).
- **Standalone load script**: `scripts/loadtest/load_orchestrator.py` with httpx.AsyncClient, outputs QPS/P50/P95/P99.
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider -m "not load"` -> 70 passed, 4 deselected, 14 subtests passed.
  - `.\.venv\Scripts\python.exe -m pytest tests\test_load_orchestrator.py -q -m load` -> 4 passed.
  - `.\.venv\Scripts\ruff.exe check src tests` -> All checks passed.
  - `.\.venv\Scripts\mypy.exe src` -> Success: no issues found in 20 source files.
  - `.\init.cmd --check-only --skip-tests` -> no failures.
  - `node scripts/harness/validate-harness.mjs` -> 100/100, no new warnings.

## AI 驱动客服利润引擎 (cs-profit-engine Task 1–10) - 2026-07-19

将客服系统从「被动响应」升级为「主动创收」，新增 9 个源模块、9 张数据表、1 个 Alembic 迁移、1 个 ADR，扩展 ADR-0004 L1 权限分层。

### 新增模块（9 个）

| 模块 | 任务 | 职责 |
|------|------|------|
| `src/user_profile_service.py` | Task 2 | 统一用户画像（多平台身份合并、意图标签、价值分层 low/medium/high/vip） |
| `src/demand_mining_service.py` | Task 3 | 需求挖掘引擎（规则意图分类、订单共现商品图谱、机会评分 0-1） |
| `src/recommendation_service.py` | Task 4 | 主动推荐（≤3 条 / 含话术 / 预期转化率 / 24h 去重漏斗事件） |
| `src/recommendation_agent.py` | Task 4 | L1 recommendation-agent MCP 工具封装（recommendation:write 权限） |
| `src/attribution_service.py` | Task 5 | 营收归因（4 模型 / 24h 窗口 / ROI / Top Agent / Top 话术） |
| `src/analytics_agent.py` | Task 5 | L1 analytics-agent MCP 工具封装（analytics:write 权限） |
| `src/profit_engine_hooks.py` | Task 6 | Orchestrator 异步钩子（ThreadPoolExecutor max_workers=4 / 2s 超时保护） |
| `src/degradation.py` | Task 7 | 峰值降级策略（L0/L1/L2 业务永不 shed，仅 shed profit-engine 内部 intent） |
| `src/human_handoff_upgrade.py` + `src/agent_assist_service.py` + `src/agent_routing.py` | Task 8 | 主动转人工（vip + confidence < 0.7）+ 坐席辅助 + 负载均衡路由 |

### 新增数据表与迁移

- Alembic 迁移 `alembic/versions/0005_profit_engine_schema.py`，新增 9 张表：`user_profile` / `user_identity` / `user_intent_tag` / `user_value_score` / `recommendation` / `funnel_event` / `attribution_record` / `touch_point` / `agent_assist_event`。
- SQLite 与 MySQL 双向兼容，幂等性由 `tests/test_profit_engine_migration.py` 覆盖（upgrade / downgrade / 重复 upgrade / 现有表保留）。

### 新增测试用例（113 个）

| 测试文件 | 测试数 |
|---------|--------|
| `tests/test_user_profile_service.py` | 9 |
| `tests/test_demand_mining_service.py` | 16 |
| `tests/test_recommendation_service.py` | 16 |
| `tests/test_attribution_service.py` | 16 |
| `tests/test_orchestrator_profit_integration.py` | 8 |
| `tests/test_profit_engine_migration.py` | 4 |
| `tests/test_peak_load_degradation.py` | 13 |
| `tests/test_human_handoff_upgrade.py` | 19 |
| `tests/test_profit_dashboard_api.py` | 12 |
| **合计** | **113** |

### ADR

- 新增 `docs/adr/0011-cs-profit-engine.md`：记录 7 个核心决策（新增 L1 Agent / 异步钩子 / 归因模型 / 会话上下文存储 / 峰值降级 / 主动转人工 / 看板 API 复用）+ 3 个被否备选方案（asyncio / 独立上下文表 / 拒绝 L2 intent）。

### feature_list.json 更新

新增 8 个 feature（全部 status=done），`completed_order` 同步追加：
`unified-user-profile` → `demand-mining-engine` → `proactive-recommendation` → `revenue-attribution` → `profit-engine-orchestrator-integration` → `peak-load-degradation` → `human-handoff-upgrade` → `profit-dashboard-api`。

### 价值看板 API（Task 9）

在 `src/order_api.py` 末尾新增 3 个 v1 端点（权限 `analytics:read`，限流 `LIMIT_READ` 120/min）：
- `GET /api/v1/profit-dashboard` — KPI + 营收 + 洞察三块，响应时间 < 2s
- `GET /api/v1/recommendations/funnel` — 各阶段事件数与转化率
- `GET /api/v1/attributions` — 归因记录列表 + 多模型汇总（支持 `?model=first_touch|last_touch|linear|time_decay`）

Prometheus 指标 `dashboard_latency_seconds`（Histogram，labels=endpoint）覆盖三个端点。

### Peak-load 指标（Task 7）

`src/metrics.py` 新增 4 个 Prometheus 指标：
- `cs_queue_wait_seconds`（Histogram，buckets 10ms–60s）
- `cs_degradation_active`（Gauge 0/1）
- `cs_active_requests`（Gauge）
- `cs_load_percent`（Gauge）

阈值 env 可覆盖：`CS_MAX_CONCURRENT` / `CS_QUEUE_WAIT_THRESHOLD` / `CS_LOAD_THRESHOLD`。

### Verification evidence

- `uv run pytest tests/ -q`：测试结果详见 session-handoff.md（本次 Task 10.4 验证）。
- `.\init.cmd --check-only --skip-tests`：6 阶段结果详见 session-handoff.md。
- `node scripts/harness/validate-harness.mjs`：审计结果详见 session-handoff.md。

### Open follow-ups

- 运维需明确归因默认模型（当前 `last_touch`）；如需切换为 `time_decay` 或 `first_touch`，更新 `attribution_service.DEFAULT_MODEL` 或通过运营配置覆盖。
- Prometheus 告警阈值需配置：`cs_degradation_active == 1` 持续 > 5min 触发告警；`cs_queue_wait_seconds` P95 > 30s 触发告警。
- `PROMETHEUS_MULTIPROC_DIR` 在多 worker 部署时需配置（ADR-0010 待办延续）。
- `agent_routing.AgentRouter` 状态不持久化，进程重启后坐席需重新注册（设计意图，避免路由到离线坐席）。
- 长期对话的 `customer_service_usage_events.intents` JSON 字段可能膨胀，需运营定期归档或后续迁移到独立表（ADR-0011 备选方案 B 预留路径）。
