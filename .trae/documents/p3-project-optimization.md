# P3: 项目优化 — 配置加固、可观测性、安全防护

## 摘要

P0/P1/P2 全部完成后,审查识别出 3 个阶段共 10 项优化。Phase A(7 项快速胜出)成本极低、相互独立;Phase B(2 项)是生产可观测性与安全主线;Phase C(2 项)是工具链收敛,可延后。

## 当前状态分析

### 认知修正(Plan Agent 核实代码后发现)

| 初始判断 | 实际情况 | 修正 |
|----------|----------|------|
| `/api/ready` 不检查 DB 连通性 | 第 144-181 行已执行 `SELECT 1` | 真正问题:degraded 时仍返回 200 + 探针含 `inspect().get_table_names()` 太重 |
| 6 处 bare-except 都需收窄 | `database.py:87` 和 `order_api.py:80` 是 rollback+raise 正确模式 | 仅 4 处需收窄(orchestrator_runtime 3 处 + kb_service 1 处) |
| 17 张表 | 实际 15 张(`Base.metadata.tables`) | CI 只断言 4/15 ≈ 27% |

### 优化项优先级矩阵

| # | 优化项 | 影响 | 成本 | 阶段 |
|---|--------|------|------|------|
| A1 | coverage 配置 + pytest-cov | 中 | 极低 | A |
| A2 | pip-audit 依赖漏洞扫描 | 中高 | 极低 | A |
| A3 | migration-smoke 全表断言(4→15) | 高 | 低 | A |
| A4 | /api/ready 状态码修正 + 探针瘦身 | 中高 | 极低 | A |
| A5 | OpenAPI 文档自定义 | 低 | 极低 | A |
| A6 | 收窄 4 处 bare-except | 中 | 低 | A |
| A7 | CI 并发测试治理(load 标记) | 中 | 低 | A |
| B1 | 结构化日志(structlog) | 高 | 中 | B |
| B2 | API 限流(slowapi) | 高 | 中 | B |
| C1 | 迁移 prometheus_client | 中低 | 中 | C |

## Phase A — 快速胜出(配置与 CI 加固)

### A1. 覆盖率配置 + pytest-cov

**文件**:`pyproject.toml`、`.github/workflows/ci.yml`

- dev 依赖加 `pytest-cov>=5.0`
- 新增 `[tool.coverage.run]`(source=src, branch=true, omit seed/server)和 `[tool.coverage.report]`(show_missing, fail_under=70 起步)
- CI test job 改为 `pytest -q -m "not load" --cov --cov-report=term-missing --cov-report=xml`
- **先跑一次量化基线**,门槛设为基线 -5%,再渐进上调

### A2. 依赖漏洞扫描

**文件**:`pyproject.toml`(dev 加 `pip-audit`)、`ci.yml`(新增 audit job)

- `uv run pip-audit --strict`
- 初期噪音大时 `--ignore-vuln <id>` 白名单过渡,每个忽略项在 `progress.md` 记录原因与时限

### A3. migration-smoke 全表断言

**文件**:`ci.yml` 第 74-83 行

- 从 `Base.metadata.tables.keys()` 派生期望表集合,手动补 `sequence_counters`(不在 model 中)
- 消除"表清单漂移"——以后新增表无需同步改 CI

### A4. /api/ready 状态码修正 + 探针瘦身

**文件**:`src/order_api.py` 第 144-181 行

- degraded 时返回 HTTP 503(当前返回 200,编排器不摘流量)
- 移除每次调用的 `inspect().get_table_names()` 表内省(迁移完整性由 CI + 启动时 alembic 保证)
- 仅保留 `SELECT 1` + 配置检查(廉价)
- 新增测试:`test_ready_returns_503_when_db_down`

### A5. OpenAPI 文档自定义

**文件**:`src/order_api.py` 第 62 行

- 添加 title/version/description/tags
- 生产环境 `APP_ENV=production` 时 `docs_url=None`(关闭交互文档),保留 `openapi_url`
- 给 32 个路由加 `tags=[...]`

### A6. 收窄 4 处 bare-except

**文件**:`src/orchestrator_runtime.py`(784/968/997)、`src/kb_service.py`(155)

- orchestrator_runtime 3 处:`except Exception` → `except SQLAlchemyError`(analytics 写入 + 持久化状态)
- kb_service 1 处:`except Exception` → `except (OSError, ImportError, RuntimeError)`(模型加载回退)
- `database.py:87` 和 `order_api.py:80` **保持不动**(rollback+raise 是正确模式)

### A7. CI 并发测试治理

**文件**:`ci.yml`

- test job 统一用 `-m "not load"`(与本地一致)
- 新增独立 `load-test` job(`continue-on-error: true`,信息性不阻塞)

## Phase B — 生产可观测性与限流

### B1. 结构化日志(structlog)+ request_id 贯通

**文件**:新增 `src/logging_config.py`、改 `src/order_api.py`

- 生产输出 JSON,开发输出彩色 Console
- 中间件绑定 `request_id`/`path`/`status_code`/`duration` 到 contextvars
- 桥接现有 `LOGGER.warning(..., exc_info=True)` 调用,无需逐处改写
- **关键约束**:MCP server 路径(`server_customer.py`)是 stdio,不挂 stdout handler

### B2. API 限流(slowapi)

**文件**:`src/order_api.py`、`pyproject.toml`(加 `slowapi>=0.1.9`)

| 路由 | 限流 | 理由 |
|------|------|------|
| `/api/auth/otp/request` | 5/minute | 短信/邮件触发点,防刷 |
| `/api/orchestrator/respond` | 60/minute | LLM 编排成本高 |
| 写端点 | 30/minute | 防批量刷单 |
| 只读查询 | 120/minute | 宽松 |

- 多实例部署 `storage_uri` 必须指向 Redis(ADR 文档化)
- 新增测试:`test_rate_limit_blocks_excess_otp`

## Phase C — 工具链收敛(可选,延后)

### C1. 迁移 prometheus_client

**文件**:重写 `src/metrics.py`、改 `src/order_api.py`

- `Histogram`/`Counter`/`Gauge` 替代手写格式
- 附带修复 P1 遗留的 gauge-as-counter 语义不纯
- `/api/metrics` 返回 `generate_latest()` + `CONTENT_TYPE_LATEST`

### C2. pytest-xdist 并行加速(需验证)

- 先小范围 `pytest -n auto --dist loadgroup` 验证无状态泄漏
- 若 unittest 模块级共享状态导致 flaky,则放弃或仅按文件并行

## 假设与决策

- **fail_under=70 是起点**:先量化基线再设门槛,避免 CI 立即红
- **Phase A 单 PR**:7 项相互独立、低风险,合并为一个 "CI & tooling" PR
- **Phase B 日志先行**:限流的 429 异常需走结构化日志,故 B1 必须先于 B2
- **MCP stdio 不挂 handler**:仅 REST API 入口配置 structlog
- **slowapi 单实例 memory 可接受**:多实例必须 Redis(ADR 文档化)
- **Phase C 延后**:手写 metrics 当前能用,除非出现维护成本拐点
- **production 关闭 docs_url**:仅保留 `openapi_url` 供网关消费

## 验证步骤

1. `pytest tests/ -q -m "not load" --cov --cov-report=term-missing` → 全绿 + 覆盖率达标
2. `ruff check src tests` + `mypy src` → 0 errors(新依赖需 stub 或 ignore_missing_imports)
3. `pip-audit --strict` → 无已知漏洞(或白名单过渡)
4. `init.cmd --check-only --skip-tests` → 无失败
5. `validate-harness.mjs` → 100/100,无新警告
6. 新增测试:`test_ready_returns_503_when_db_down`、`test_rate_limit_blocks_excess_otp`
7. Phase B 新增 ADR-0008(结构化日志)、ADR-0009(限流);Phase C 新增 ADR-0010(prometheus_client)
8. 更新 `feature_list.json`、`progress.md`、`session-handoff.md`
