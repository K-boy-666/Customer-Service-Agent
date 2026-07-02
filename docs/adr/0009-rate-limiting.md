# ADR-0009: API 限流策略 (slowapi)

## 状态

已采纳 (2026-07-01)

## 背景

REST API 面向外部客户，需要限流以防止滥用和 DoS 攻击。不同端点的业务敏感度和资源消耗差异很大：

1. **OTP 发送**: 涉及短信/邮件发送成本，且可被滥用做短信轰炸，需要最严格限流
2. **Orchestrator**: 触发多 agent 编排和 LLM 调用，资源消耗高
3. **写操作**: 创建工单/退货/调研，涉及 DB 写入和编号生成
4. **读操作**: 查询订单/工单/退货，资源消耗低，可容忍较高频率
5. **健康检查**: `/api/health`、`/api/ready`、`/api/metrics` 需要随时可访问用于监控探针

此外，限流参数需要通过环境变量配置，便于生产环境不停机调优。

## 决策

采用 **slowapi + 4 层分级限流 + env-driven 配置** 方案：

### 限流器 (`src/rate_limit.py`)

```python
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_cfg.rate_limit_storage_uri,
    enabled=_cfg.rate_limit_enabled,
)
```

- `key_func=get_remote_address`: 按客户端 IP 限流
- `default_limits=[]`: 不设全局默认限流，每路由显式声明
- `storage_uri`: 支持 Redis 存储后端（多实例共享限流状态），默认 `memory://`（单实例）
- `enabled`: 全局开关，测试环境设为 `false`

### 4 层限流策略

| 层级 | 端点类型 | 默认限制 | 环境变量 | 路由数 |
|------|---------|---------|---------|--------|
| OTP | `/api/auth/otp/request`, `/api/auth/otp/verify` | 5/min | `RATE_LIMIT_OTP` | 2 |
| Orchestrator | `/api/orchestrator/respond` | 60/min | `RATE_LIMIT_ORCHESTRATOR` | 1 |
| Write | POST/PUT/DELETE `/api/*` | 30/min | `RATE_LIMIT_WRITE` | 9 |
| Read | GET `/api/*` | 120/min | `RATE_LIMIT_READ` | 14 |
| 健康检查 | `/api/health`, `/api/ready`, `/api/metrics` | 不限流 | — | 3 |

- 共 29 个路由添加 `@limiter.limit(LIMIT_*)` 装饰器
- 每个被限流的路由必须声明 `request: Request` 参数（slowapi 要求）
- 健康检查端点不添加装饰器，确保监控探针不受限流影响

### `headers_enabled` 权衡

移除 `headers_enabled=True`（默认即为 `False`）：
- slowapi 的 `SlowAPIMiddleware` 基于 `BaseHTTPMiddleware`，与 ADR-0008 的纯 ASGI 中间件共存时抛出 `"parameter response must be an instance of starlette.responses.Response"` 异常
- 移除后限流功能完全正常，仅不输出 `X-RateLimit-Remaining`、`X-RateLimit-Limit`、`Retry-After` 等响应头
- 客户端通过 HTTP 429 状态码和响应体仍可感知限流

### 测试隔离

- `conftest.py` 设置 `os.environ.setdefault("RATE_LIMIT_ENABLED", "false")`，在模块导入时禁用限流
- `RateLimitTest` 在 `setUp`/`tearDown` 中直接操作 `limiter.enabled = True/False` 和 `limiter.reset()`，实现测试级隔离（因为环境变量在模块导入时已读取，运行时修改无效）

## 理由

1. **分级匹配风险**: OTP 限流最严（5/min）因为短信轰炸风险最高；读操作限流最宽（120/min）因为资源消耗低。按业务敏感度分级比全局统一限流更合理
2. **env-driven 运维友好**: 所有限流值通过环境变量配置，生产环境可通过更新环境变量 + 滚动重启调整，无需改代码
3. **测试隔离可靠**: 直接操作 `limiter` 实例比依赖环境变量更可靠，因为 slowapi 在模块导入时读取配置
4. **健康端点排除**: 监控探针（Prometheus、k8s liveness probe）需要随时访问健康端点，限流会导致误判
5. **headers_enabled 权衡可接受**: `X-RateLimit-*` 头是信息性的，客户端主要通过 429 状态码感知限流；与纯 ASGI 中间件的兼容性优先级更高

## 后果

- 新增 `src/rate_limit.py`（Limiter 实例 + LIMIT_* 常量）
- `order_api.py` 29 个路由添加 `@limiter.limit()` 装饰器和 `request: Request` 参数
- `order_api.py` 新增 `app.state.limiter = limiter` 和 `RateLimitExceeded` 异常处理器
- `config.py` 新增 6 个限流配置字段
- `conftest.py` 设置 `RATE_LIMIT_ENABLED=false`
- `pyproject.toml` 新增 `slowapi>=0.1.9` 依赖
- `.env.example` 文档化所有 `RATE_LIMIT_*` 环境变量
- 无 `X-RateLimit-*` 响应头（headers_enabled=False）
- 测试 `test_structlog_rate_limit.py` 中 5 个限流测试验证 OTP 限流、读端点限流、健康端点排除、429 响应体
