# Phase B (P4) 文档收尾计划

## 摘要

Phase B (P4: structlog + slowapi) 的代码实现已在上一次会话中完成并通过全部验证（81 passed, 4 deselected, 14 subtests, coverage 76.30%, ruff/mypy/init.cmd/validate-harness 全绿）。本次仅需完成 5 项文档收尾任务，涉及 4 个文件的更新/创建。不涉及任何代码变更。

## 当前状态分析

### 已完成（代码 — 上一次会话）

| 文件 | 变更类型 | 内容 |
|------|---------|------|
| `src/log_config.py` | 新建 | structlog 配置, ProcessorFormatter 桥接 stdlib logging, JSON/console 渲染选择, `log_to_stderr` 参数 |
| `src/rate_limit.py` | 新建 | slowapi Limiter, env-driven 配置, 4 个 LIMIT_* 常量 |
| `src/request_logging.py` | 新建 | 纯 ASGI 中间件, contextvars 绑定 (request_id/method/path/status_code/duration), 替代原 metrics_middleware |
| `src/order_api.py` | 修改 | configure_logging() 调用, 29 路由 @limiter.limit() 装饰器, StructuredRequestLoggingMiddleware, RateLimitExceeded handler |
| `src/config.py` | 修改 | RuntimeConfig 新增 7 个字段 (log_json, rate_limit_enabled/storage_uri/otp/orchestrator/write/read) |
| `src/server_customer.py` | 修改 | main() 调用 configure_logging(log_to_stderr=True) |
| `src/api_dependencies.py` | 修改 | request_id_dependency 从 request.state 读取 |
| `tests/test_structlog_rate_limit.py` | 新建 | 10 个测试 (5 structlog + 5 slowapi) |
| `tests/conftest.py` | 修改 | `RATE_LIMIT_ENABLED=false` 环境变量 |
| `pyproject.toml` | 修改 | 新增 structlog>=26.1.0, slowapi>=0.1.9 |
| `.env.example` | 修改 | 新增 LOG_JSON, RATE_LIMIT_* 环境变量文档 |
| `feature_list.json` | 修改 | 新增 structured-logging-rate-limiting 条目, status=done |

### 未完成（文档 — 本次任务）

| # | 文件 | 任务 | 状态 |
|---|------|------|------|
| 1 | `progress.md` | 追加 P4 验证证据小节 | 未完成（header/recent completions/planned next 已更新） |
| 2 | `session-handoff.md` | 追加 P4 session block | 未完成（最后 block 为 P3 Phase A） |
| 3 | `docs/adr/0008-structured-logging.md` | 新建 ADR | 未完成（ADR 仅到 0007） |
| 4 | `docs/adr/0009-rate-limiting.md` | 新建 ADR | 未完成 |
| 5 | `AGENTS.md` | Key Paths 表添加新模块 | 未完成（当前表无 log_config/rate_limit/request_logging） |

## 拟议变更

### 变更 1: progress.md — 追加 P4 验证证据小节

- **位置**: 文件末尾（P2 小节之后，即当前第 168 行之后）
- **格式**: 遵循 P3 小节模式
- **内容要点**:
  - `## Structured logging + API rate limiting (P4 Phase B) - 2026-07-01`
  - structlog: ProcessorFormatter 桥接, shared_processors 链, JSONRenderer/ConsoleRenderer, contextvars (request_id/method/path/status_code/duration_ms)
  - 纯 ASGI 中间件: 替代 BaseHTTPMiddleware, 确保 contextvars 跨同步/异步边界传播
  - slowapi: 4 层限流 (OTP 5/min, orchestrator 60/min, write 30/min, read 120/min), env-driven, 29 路由装饰, 3 个健康端点排除
  - MCP 日志: `log_to_stderr=True`, stdout 保留给 JSON-RPC 协议
  - 测试隔离: conftest.py `RATE_LIMIT_ENABLED=false`, RateLimitTest 直接操作 `limiter.enabled`/`limiter.reset()`
  - 权衡: `headers_enabled` 移除（与纯 ASGI 中间件不兼容, 限流功能不受影响）
  - Verification evidence: 81 passed / ruff / mypy 23 files / init.cmd / validate-harness 100/100

### 变更 2: session-handoff.md — 追加 P4 session block

- **位置**: 文件末尾（P3 session block 之后）
- **格式**: 遵循现有 session block 模式（Branch / Active feature / Outcome / What was done / Verification evidence / Open follow-ups）
- **内容要点**:
  - Branch: `main`
  - Active feature: P4 Phase B — structlog + slowapi
  - Outcome: Implemented; all tests pass; all checks green
  - What was done: 列出全部代码变更（log_config.py, rate_limit.py, request_logging.py, order_api.py 29 路由, config.py 7 字段, server_customer.py, api_dependencies.py, test_structlog_rate_limit.py 10 测试, conftest.py, pyproject.toml, .env.example）
  - Verification evidence: 81 passed / coverage 76.30% / ruff / mypy 23 files / init.cmd / validate-harness 100/100
  - Open follow-ups: Phase C (P5) pending; headers_enabled 权衡; prometheus_client 直方图迁移未做

### 变更 3: docs/adr/0008-structured-logging.md — 新建 ADR

- **格式**: 遵循 ADR-0006/0007 模式（中文标题, 状态/背景/决策/理由/后果 五段式）
- **内容要点**:
  - 标题: `ADR-0008: 结构化日志 (structlog) 与请求追踪`
  - 状态: 已采纳 (2026-07-01)
  - 背景: 生产环境需要结构化 JSON 日志用于可观测性；现有 `logging.getLogger(__name__)` 调用分布在 orchestrator_runtime.py 等模块；MCP stdio 协议要求 stdout 保留给 JSON-RPC
  - 决策:
    - structlog 26.x + ProcessorFormatter 桥接 stdlib logging
    - shared_processors 链: merge_contextvars → add_logger_name → add_log_level → TimeStamper → StackInfoRenderer → format_exc_info → UnicodeDecoder → CallsiteParameterAdder
    - 生产 JSONRenderer, 开发 ConsoleRenderer(colors=True)
    - 纯 ASGI 中间件 (非 BaseHTTPMiddleware) 绑定 contextvars
    - MCP 服务器 `log_to_stderr=True`
  - 理由: ProcessorFormatter `foreign_pre_chain` 无需修改现有 LOGGER 调用; 纯 ASGI 避免 BaseHTTPMiddleware 子任务 contextvars 丢失; `handlers.clear()` 保证幂等
  - 后果: 新增 `src/log_config.py`, `src/request_logging.py`; `order_api.py` 模块级调用 `configure_logging()`; `server_customer.py` 传入 `log_to_stderr=True`

### 变更 4: docs/adr/0009-rate-limiting.md — 新建 ADR

- **格式**: 同上
- **内容要点**:
  - 标题: `ADR-0009: API 限流策略 (slowapi)`
  - 状态: 已采纳 (2026-07-01)
  - 背景: API 需要限流防止滥用和 DoS; 不同端点敏感度不同 (OTP 发送 vs 只读查询); 需要环境变量驱动以便生产调优
  - 决策:
    - slowapi Limiter, `key_func=get_remote_address`
    - 4 层限流: OTP 5/min, orchestrator 60/min, write 30/min, read 120/min
    - 3 个健康端点 (`/api/health`, `/api/ready`, `/api/metrics`) 排除限流
    - env-driven: `RATE_LIMIT_OTP/ORCHESTRATOR/WRITE/READ`, `RATE_LIMIT_ENABLED`, `RATE_LIMIT_STORAGE_URI`
    - `headers_enabled` 移除 (与纯 ASGI 中间件不兼容)
  - 理由: 按端点类型分级匹配业务风险; env-driven 便于不停机调优; 测试隔离通过 `limiter.enabled`/`limiter.reset()` 而非环境变量
  - 后果: 新增 `src/rate_limit.py`; 29 路由添加 `@limiter.limit()` + `request: Request` 参数; `conftest.py` 设置 `RATE_LIMIT_ENABLED=false`; 无 X-RateLimit-* 响应头

### 变更 5: AGENTS.md — 更新 Key Paths 表

- **位置**: Key Paths 表（当前行 88-104）
- **变更**: 在表中添加 3 行（插入在 `tests/` 行之前）:

```
| `src/log_config.py` | structlog 配置与 stdlib logging 桥接 |
| `src/rate_limit.py` | slowapi 限流器配置与 LIMIT_* 常量 |
| `src/request_logging.py` | 纯 ASGI 请求日志中间件 (contextvars 绑定) |
```

## 假设与决策

1. **代码无需重新实现**: 上一次会话已完成全部代码变更并通过验证
2. **文档遵循仓库现有模式**: ADR 使用中文五段式（状态/背景/决策/理由/后果）; session block 使用现有字段格式; progress.md 验证证据遵循 P2/P3 小节模式
3. **ADR 使用中文撰写**: 匹配 ADR-0006/0007 风格
4. **仅文档变更**: 不修改任何 `.py` 源文件
5. **验证证据复用**: 使用上一次会话的测试结果（81 passed, coverage 76.30%），计划完成后重新运行确认无回归

## 验证步骤

1. **测试回归**: `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider -m "not load" --cov --cov-report=term-missing` → 期望 81 passed, coverage ≥76%
2. **Lint 检查**: `.\.venv\Scripts\ruff.exe check src tests` → 期望 All checks passed
3. **类型检查**: `.\.venv\Scripts\mypy.exe src` → 期望 Success: no issues in 23 source files
4. **Init 检查**: `.\init.cmd --check-only --skip-tests` → 期望 no failures
5. **Harness 验证**: `node scripts/harness/validate-harness.mjs` → 期望 100/100, no new warnings
6. **文档格式审查**: 目视检查 ADR/session block/progress 小节格式与现有文档一致
