# P4 Phase B: 结构化日志(structlog) + API 限流(slowapi)

## 摘要

为生产 REST API 添加两项关键能力:(1) structlog 结构化日志,生产输出 JSON、开发输出彩色,通过 contextvars 自动注入 request_id 到所有日志条目;(2) slowapi 逐路由限流,OTP 5/min、编排器 60/min、写端点 30/min、只读 120/min。

## 当前状态分析

### 日志现状
- 3 个文件用 `logging.getLogger(__name__)`:order_api.py(1处)、orchestrator_runtime.py(3处),均 `LOGGER.warning(..., exc_info=True)`
- config.py 已有 `log_level` 字段和 `is_production()` 函数
- 无结构化输出,无 request_id 贯通

### request_id 现状
- `api_dependencies.py` 的 `request_id_dependency` 读 `X-Request-ID` header,仅字符串传递,未入 contextvars
- 已贯穿 12 个路由的函数参数,但不出现在日志中

### 中间件现状
- `order_api.py` 的 `metrics_middleware`(async,基于 `@app.middleware("http")`)记录请求耗时
- 底层是 BaseHTTPMiddleware,contextvars 传播有已知不可靠问题(FastAPI #5999)

### MCP server
- `server_customer.py` 用 stdio 传输 JSON-RPC,stdout 被协议占用,日志必须走 stderr

### 路由现状
- 32 个路由全部是同步 `def`(非 `async def`),FastAPI 通过线程池执行
- OTP/orchestrator 端点缺 `request: Request` 参数(slowapi 必需)

## 架构决策

### 决策 1: ProcessorFormatter 统一渲染
现有 4 处 `LOGGER.warning(...)` 调用不改动,通过 `structlog.stdlib.ProcessorFormatter` 的 `foreign_pre_chain` 包含 `merge_contextvars`,让 stdlib 日志自动注入 request_id/path 等上下文字段。structlog 和 stdlib 两条日志路径经过同一渲染管线。

### 决策 2: 纯 ASGI 中间件(非 BaseHTTPMiddleware)
`@app.middleware("http")` 底层用 BaseHTTPMiddleware,为每个请求创建子任务,contextvars 传播不可靠。改用纯 ASGI 中间件在同一个 async 上下文中运行整个请求生命周期,contextvars 传播可预测。同时合并现有 `metrics_middleware` 的指标记录功能。

### 决策 3: 逐路由限流 + 环境变量配置
四档限流逐路由 `@limiter.limit()` 装饰,健康检查不限流。限流值从 `RATE_LIMIT_*` 环境变量读取,部署可调。

### 决策 4: MCP server 日志走 stderr
`server_customer.py` 的 stdout 被 JSON-RPC 协议占用,通过 `configure_logging(log_to_stderr=True)` 参数控制日志输出到 stderr。

### 决策 5: 测试默认禁用限流
`conftest.py` 设 `RATE_LIMIT_ENABLED=false`,避免 71 个现有测试因限流失败。限流逻辑通过专用测试验证。

## 实施步骤

### 步骤 1: 依赖声明与配置扩展

**文件**: `pyproject.toml`
- dependencies 添加 `structlog>=26.1.0`、`slowapi>=0.1.9`

**文件**: `src/config.py`
- `RuntimeConfig` 新增 7 个字段:`log_json`、`rate_limit_enabled`、`rate_limit_storage_uri`、`rate_limit_otp`、`rate_limit_orchestrator`、`rate_limit_write`、`rate_limit_read`
- `load_runtime_config()` 读取对应环境变量,默认值:`5/minute`、`60/minute`、`30/minute`、`120/minute`
- `log_json` 逻辑:生产环境默认 True,开发默认 False,可通过 `LOG_JSON` 覆盖

**文件**: `.env.example`
- 追加 `LOG_JSON`、`RATE_LIMIT_ENABLED`、`RATE_LIMIT_STORAGE_URI`、`RATE_LIMIT_OTP`、`RATE_LIMIT_ORCHESTRATOR`、`RATE_LIMIT_WRITE`、`RATE_LIMIT_READ`

### 步骤 2: 新建 structlog 配置模块

**新文件**: `src/log_config.py`

核心函数 `configure_logging(log_level, json_logs, log_to_stderr)`:
- 共享处理器链:`merge_contextvars` → `add_logger_name` → `add_log_level` → `TimeStamper(iso, utc)` → `StackInfoRenderer` → `format_exc_info` → `UnicodeDecoder` → `CallsiteParameterAdder`
- structlog 末端:`ProcessorFormatter.wrap_for_formatter`
- 渲染器:`JSONRenderer`(生产)或 `ConsoleRenderer`(开发)
- `ProcessorFormatter(foreign_pre_chain=shared_processors, processors=[renderer])` 桥接 stdlib
- `handlers.clear()` 保证幂等
- uvicorn.access 日志降级到 WARNING

### 步骤 3: 新建请求日志中间件(纯 ASGI)

**新文件**: `src/request_logging.py`

`StructuredRequestLoggingMiddleware` 类:
- 请求前:`clear_contextvars()` → `bind_contextvars(request_id, method, path)`;request_id 优先读 `X-Request-ID` header,缺失则生成 UUID
- `scope["state"]["request_id"]` 写入,供 `request_id_dependency` 读取
- `send_wrapper` 捕获 status_code(`http.response.start` 消息)
- 请求后:`bind_contextvars(status_code, duration_ms)` → `logger.info("http_request", ...)` → `record_request(path, start)`
- 替代现有 `metrics_middleware`

### 步骤 4: 修改 request_id_dependency

**文件**: `src/api_dependencies.py`
- 改为 `return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID", "")`
- 从中间件 state 读取,回退到 header

### 步骤 5: 新建 slowapi 限流模块

**新文件**: `src/rate_limit.py`
- `Limiter(key_func=get_remote_address, default_limits=[], storage_uri=cfg.rate_limit_storage_uri, enabled=cfg.rate_limit_enabled, headers_enabled=True)`
- 4 个限流常量:`LIMIT_OTP`、`LIMIT_ORCHESTRATOR`、`LIMIT_WRITE`、`LIMIT_READ`

### 步骤 6: order_api.py 集成

**文件**: `src/order_api.py`

6.1 顶部导入 `Request`、`configure_logging`、`limiter`、`LIMIT_*`、`StructuredRequestLoggingMiddleware`、`_rate_limit_exceeded_handler`、`RateLimitExceeded`;在 LOGGER 创建前调用 `configure_logging()`

6.2 注册:`app.state.limiter = limiter`、`app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)`、`app.add_middleware(StructuredRequestLoggingMiddleware)`;删除原 `metrics_middleware`

6.3 29 个路由添加 `@limiter.limit()` + `request: Request` 参数:
- 健康检查(3个):不限流
- OTP(2个):`@limiter.limit(LIMIT_OTP)`
- 编排器(1个):`@limiter.limit(LIMIT_ORCHESTRATOR)`
- 写端点(9个):`@limiter.limit(LIMIT_WRITE)`
- 只读(14个):`@limiter.limit(LIMIT_READ)`

### 步骤 7: MCP server 日志适配

**文件**: `src/server_customer.py`
- `main()` 中添加 `configure_logging(log_level=cfg.log_level, json_logs=cfg.log_json, log_to_stderr=True)`

### 步骤 8: 测试

**新文件**: `tests/test_structlog_rate_limit.py`

structlog 测试(5个):
1. `test_structlog_json_output_in_production` — LOG_JSON=true,验证 JSON 格式 + request_id 字段
2. `test_structlog_console_output_in_development` — LOG_JSON=false,验证彩色格式
3. `test_request_id_propagates_to_orchestrator_logger` — 带 X-Request-ID 请求,验证 orchestrator_runtime LOGGER.warning 包含相同 request_id
4. `test_request_id_generated_when_missing` — 不传 header,验证自动生成 UUID
5. `test_stdlib_logging_bridged` — 调用 stdlib logging,验证格式与 structlog 一致

slowapi 测试(7个):
6. `test_otp_rate_limit_5_per_minute` — 6 次 OTP,第 6 次返回 429
7. `test_orchestrator_rate_limit_60_per_minute` — 61 次,第 61 次返回 429
8. `test_write_endpoint_rate_limit_30_per_minute` — 31 次,第 31 次返回 429
9. `test_read_endpoint_rate_limit_120_per_minute` — 121 次,第 121 次返回 429
10. `test_health_endpoints_not_rate_limited` — 200 次 health,全 200
11. `test_rate_limit_headers_present` — 验证 X-RateLimit-* 响应头
12. `test_rate_limit_429_body_format` — 验证 429 响应体格式

**文件**: `tests/conftest.py` — 添加 `os.environ.setdefault("RATE_LIMIT_ENABLED", "false")`

### 步骤 9: 文档更新

- `feature_list.json`:新增 `structured-logging-rate-limiting` feature 条目
- `progress.md`:追加 Phase B 完成验证记录
- `session-handoff.md`:追加 Session 块
- `AGENTS.md`:Key Paths 添加新模块路径
- 新增 `docs/adr/0008-structured-logging.md`、`docs/adr/0009-rate-limiting.md`

## 假设与决策

- **structlog 26.x**:要求 Python >=3.10,项目要求 3.10+ 兼容
- **ProcessorFormatter + wrap_for_formatter**:不能同时用 `render_to_log_kwargs`,structlog 处理器链末端必须用 `wrap_for_formatter`
- **contextvars 传播**:路由全部同步 `def`,FastAPI 通过 `anyio.to_thread.run_sync` 在线程池执行,该函数 `copy_context()` 后在新线程运行,中间件 async 上下文中绑定的 contextvars 会传播到同步路由
- **slowapi 内存存储**:单实例用 memory://,生产多实例必须配 `RATE_LIMIT_STORAGE_URI=redis://...`(ADR 文档化)
- **测试默认禁用限流**:`conftest.py` 设 `RATE_LIMIT_ENABLED=false`,限流测试在专用 fixture 中临时启用
- **ConsoleRenderer 颜色**:非 TTY 环境自动禁用颜色,CI 日志可读

## 验证步骤

1. `pip install structlog slowapi` 安装依赖
2. `pytest tests/ -q -m "not load"` 全绿(71+12 = 83 passed)
3. `ruff check src tests` 通过
4. `mypy src` 通过(新模块类型标注完整)
5. `init.cmd --check-only --skip-tests` 无失败
6. `validate-harness.mjs` 100/100 无新警告
7. 手动验证:生产模式启动,日志输出 JSON 含 request_id;开发模式启动,日志输出彩色

## 文件变更清单

| 文件 | 操作 |
|------|------|
| `pyproject.toml` | 修改:添加依赖 |
| `src/config.py` | 修改:新增 7 个配置字段 |
| `.env.example` | 修改:追加配置项 |
| `src/log_config.py` | 新建:structlog 配置 |
| `src/request_logging.py` | 新建:纯 ASGI 中间件 |
| `src/rate_limit.py` | 新建:slowapi Limiter |
| `src/api_dependencies.py` | 修改:request_id 读 state |
| `src/order_api.py` | 修改:集成日志+限流,29 路由加装饰器 |
| `src/server_customer.py` | 修改:MCP 日志走 stderr |
| `tests/test_structlog_rate_limit.py` | 新建:12 个测试 |
| `tests/conftest.py` | 修改:禁用限流默认值 |
| `feature_list.json` | 修改:新增 feature 条目 |
| `progress.md` | 修改:追加完成记录 |
| `session-handoff.md` | 修改:追加 Session 块 |
| `docs/adr/0008-structured-logging.md` | 新建 |
| `docs/adr/0009-rate-limiting.md` | 新建 |

**不改动**: `orchestrator_runtime.py`(3处 LOGGER.warning 自动通过 ProcessorFormatter 桥接获得结构化输出)
