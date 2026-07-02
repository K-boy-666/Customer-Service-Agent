# ADR-0008: 结构化日志 (structlog) 与请求追踪

## 状态

已采纳 (2026-07-01)

## 背景

生产环境需要结构化 JSON 日志用于可观测性（ELK/Loki 等日志聚合系统能直接解析）。现有 `logging.getLogger(__name__)` 调用分布在 `orchestrator_runtime.py`、`service_layer.py` 等多个模块中，逐个迁移成本高且易遗漏。此外：

1. **请求追踪**: 需要将 `request_id` 贯穿整个请求处理链路（中间件 → 路由 → 业务逻辑 → 日志输出），便于跨服务排障。
2. **MCP stdio 协议**: `server_customer.py` 通过 stdout 传输 JSON-RPC 消息，日志不能输出到 stdout，否则破坏协议。
3. **环境差异**: 开发环境需要人类可读的彩色控制台输出，生产环境需要机器可解析的 JSON。

## 决策

采用 **structlog 26.x + ProcessorFormatter 桥接 + 纯 ASGI 中间件 + contextvars** 方案：

### structlog 配置 (`src/log_config.py`)

`configure_logging()` 函数提供统一配置入口：

- **shared_processors 链**: `merge_contextvars` → `add_logger_name` → `add_log_level` → `PositionalArgumentsFormatter` → `TimeStamper(fmt="iso", utc=True)` → `StackInfoRenderer` → `format_exc_info` → `UnicodeDecoder` → `CallsiteParameterAdder`
- **structlog.configure**: processors 末尾使用 `ProcessorFormatter.wrap_for_formatter` 作为终止符，`wrapper_class` 使用 `make_filtering_bound_logger(level)`，`cache_logger_on_first_use=True`
- **渲染器**: 生产环境 `JSONRenderer()`，开发环境 `ConsoleRenderer(colors=True)`
- **ProcessorFormatter**: `foreign_pre_chain=shared_processors` 桥接 stdlib `logging.getLogger()` 调用，使其自动经过相同的 contextvars 合并和处理器链
- **幂等性**: `root_logger.handlers.clear()` 确保多次调用不会叠加 handler
- **uvicorn access 日志**: `logging.getLogger("uvicorn.access").setLevel(logging.WARNING)` 减少噪音

### 纯 ASGI 中间件 (`src/request_logging.py`)

`StructuredRequestLoggingMiddleware` 替代原 `metrics_middleware`：

- 使用纯 ASGI 实现（非 `BaseHTTPMiddleware` 子类），避免子任务导致 contextvars 丢失
- `__call__` 中 `clear_contextvars()` → `bind_contextvars(request_id, method, path)`
- `send_wrapper` 捕获 `http.response.start` 中的 `status_code`
- `finally` 块绑定 `status_code`、`duration_ms`，输出 `logger.info("http_request")`，调用 `record_request()` 记录指标
- `request_id` 来源: `X-Request-ID` 请求头，缺失时生成 `uuid.uuid4().hex`
- `request_id` 同时写入 `scope["state"]["request_id"]`，供 `request_id_dependency` 读取

### MCP 服务器日志

`server_customer.py` 的 `main()` 调用 `configure_logging(log_to_stderr=True)`：
- `log_to_stderr=True` 使 `StreamHandler` 输出到 `sys.stderr`
- stdout 完全保留给 JSON-RPC 协议，避免日志破坏 MCP 通信

## 理由

1. **零侵入桥接**: ProcessorFormatter 的 `foreign_pre_chain` 包含 `merge_contextvars`，使 `orchestrator_runtime.py` 中已有的 `LOGGER.warning()` 调用自动获得 `request_id` 等 contextvars 字段，无需修改业务代码
2. **纯 ASGI 可靠性**: `BaseHTTPMiddleware` 在子任务中创建新的 task context，可能导致 contextvars 丢失；纯 ASGI 中间件在同一个 task 中执行，确保 contextvars 贯穿整个请求
3. **环境适配**: `LOG_JSON` 环境变量控制渲染器选择，生产默认 JSON，开发默认 console，支持运行时覆盖
4. **幂等配置**: `handlers.clear()` 防止测试中多次导入 `order_api` 时叠加重复 handler
5. **调用点信息**: `CallsiteParameterAdder` 添加 `filename`/`func_name`/`lineno`，便于定位日志来源

## 后果

- 新增 `src/log_config.py`（配置入口）和 `src/request_logging.py`（ASGI 中间件）
- `order_api.py` 模块级调用 `configure_logging()`，替换原 `metrics_middleware` 为 `StructuredRequestLoggingMiddleware`
- `server_customer.py` 传入 `log_to_stderr=True`
- `api_dependencies.py` 的 `request_id_dependency` 从 `request.state.request_id` 读取
- `config.py` 新增 `log_json` 配置字段
- `pyproject.toml` 新增 `structlog>=26.1.0` 依赖
- 测试 `test_structlog_rate_limit.py` 验证 JSON 输出、console 输出、request_id 传播、stdlib 桥接
