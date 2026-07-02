# Phase C (P5): prometheus_client 迁移 + pytest-xdist 并行加速

## 摘要

Phase C 包含两个独立优化任务：(1) 用 `prometheus_client` 标准库替换自定义 `MetricsRegistry`，使 `/api/metrics` 输出标准 Prometheus exposition format 并支持多进程聚合；(2) 添加 `pytest-xdist` 并行运行测试，将 81 个测试的执行时间从 ~63s 降至 ~20s。两个任务相互独立，先实施 xdist（风险更低、不涉及业务代码），再实施 prometheus_client 迁移。

## 当前状态分析

### 自定义 MetricsRegistry (`src/metrics.py`)

- `MetricsRegistry` 类：线程安全 dict + 手写 histogram 桶 `(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)`
- 全局 `REGISTRY` 实例 + `record_request(route, start_time)` 函数
- `request_logging.py:77` 调用 `record_request(path, start)` 记录延迟
- `order_api.py:208-235` 的 `/api/metrics` 端点手动拼接 Prometheus 文本：2 个 counter（conversations/handoffs）+ 3 个 gauge（tickets/returns/surveys）+ `REGISTRY.render()` histogram 行
- 已有 Grafana dashboard 和 alert rules 引用现有指标名

### 测试套件现状

- 81 个测试，17 个 `unittest.TestCase` 类，17 个文件各含 1 个类
- 串行运行 ~63s
- 全部使用 setUp/tearDown 模式操作全局状态：`DATABASE_URL` env var、`database._engine`、`_CONVERSATION_STATES`、`_LOCAL_SEQ`、`limiter`
- `conftest.py` 提供 `temp_db`/`actor`/`verification_token` 函数级 fixture
- 临时 SQLite 文件通过 `tempfile.mkstemp` 生成唯一路径
- CI: `uv run pytest tests/ -q -m "not load" --cov --cov-report=term-missing --cov-report=xml`

## 拟议变更

### 变更 1: 添加依赖到 `pyproject.toml`

- **文件**: `pyproject.toml`
- **变更**: `dependencies` 添加 `prometheus_client>=0.20.0`；`dev` 依赖添加 `pytest-xdist>=3.5`
- **理由**: prometheus_client 0.20+ 有完善的多进程模式支持；pytest-xdist 3.5 兼容 pytest 8.x/9.x

### 变更 2: 添加 pytest-xdist 并行测试（先实施，风险低）

#### 2.1 更新 CI 测试命令

- **文件**: `.github/workflows/ci.yml`
- **变更**: test job 第 48 行命令改为 `uv run pytest tests/ -q -m "not load" -n auto --dist=loadscope --cov --cov-report=term-missing --cov-report=xml`
- **不修改**: load-test job（第 63 行）保持串行，负载测试需要准确的 QPS/延迟数据
- **分发策略**: `--dist=loadscope` 按 TestCase 类分组，同一类所有方法在同一 worker 执行。对本项目 17 文件各含 1 类的结构等价于按文件分组。这是 pytest-xdist 官方对 `unittest.TestCase` 的推荐策略。

#### 2.2 更新 init_check.py 测试命令

- **文件**: `scripts/harness/init_check.py`
- **变更**: pytest 命令添加 `-n auto --dist=loadscope`，让本地 init 检查也享受并行加速

#### 2.3 全局状态隔离分析（无需修改任何测试文件或 conftest.py）

**核心结论**: 每个 xdist worker 是独立 OS 进程，所有全局状态自动隔离。

| 全局状态 | 隔离机制 | 需要修改 |
|---------|---------|---------|
| `os.environ["DATABASE_URL"]` | 进程级 env，worker 间独立 | 否 |
| `database._engine` / `_SessionLocal` | 模块级全局，每 worker 独立加载 | 否 |
| `_CONVERSATION_STATES` dict | 模块级全局，每 worker 独立 | 否 |
| `_LOCAL_SEQ` dict (numbering) | 模块级全局，每 worker 独立 | 否 |
| `limiter` 实例 (slowapi) | 模块级全局，每 worker 独立创建 | 否 |
| `REGISTRY` (prometheus) | 模块级全局，每 worker 独立 | 否 |
| 临时 SQLite 文件路径 | `tempfile.mkstemp` 生成唯一路径 | 否 |
| `RATE_LIMIT_ENABLED` env | conftest 模块加载时 `setdefault`，每 worker 独立 | 否 |

**`RateLimitTest` 特殊处理**: setUp 设 `limiter.enabled=True`，tearDown 设 `limiter.enabled=False`。由于 `loadscope` 将整个 `RateLimitTest` 类分配到同一 worker 且类内串行执行，setUp/tearDown 正确切换状态，不影响同 worker 其他类。

**`test_harness_risk_controls.py` 的 `sys.modules.pop`**: 该测试使用 `sys.modules.pop("server")` + `importlib.import_module`。由于 `loadscope` 将该类分配到单独 worker 且类内串行执行，`sys.modules` 操作不跨 worker 影响。无需修改。

### 变更 3: 重写 `src/metrics.py` 为 prometheus_client 标准类型

- **文件**: `src/metrics.py`
- **变更**: 整个文件替换为基于 `prometheus_client` 的实现
- **保留**: `record_request(route, start_time)` 函数签名不变，使 `request_logging.py` 无需修改
- **指标定义**:
  - `Histogram("http_request_duration_seconds", ..., ["route"], buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))` — 指标名和 label 名与原始实现完全一致
  - 5 个 `Gauge` 类型 DB-count 指标：`CONVERSATIONS_TOTAL`、`HANDOFFS_TOTAL`、`TICKETS_TOTAL`、`RETURNS_TOTAL`、`SURVEYS_TOTAL`，均设置 `multiprocess_mode='mostrecent'`
  - 所有指标名保留原有名称（含 `_total` 后缀），保证 Grafana dashboard/alerts 向后兼容
- **设计决策**:
  - DB-count 指标从 counter 改为 gauge（反映当前 DB 状态而非单调递增）
  - `multiprocess_mode='mostrecent'`：多进程模式下 scrape 端点 worker 查询 DB 后 `.set()`，聚合时取最新值；单进程模式忽略此参数
  - 移除 `MetricsRegistry` 类和 `REGISTRY` 全局实例

### 变更 4: 修改 `/api/metrics` 端点

- **文件**: `src/order_api.py`
- **变更**:
  - 导入: 移除 `from metrics import REGISTRY`，改为导入 5 个 Gauge 实例 + `prometheus_client` 的 `CONTENT_TYPE_LATEST`、`CollectorRegistry`、`generate_latest`、`multiprocess`；添加 `import os` 和 `from fastapi import Response`
  - 端点函数（第 208-235 行）替换:
    - 移除 `response_class=PlainTextResponse`，返回 `Response(content=data, media_type=CONTENT_TYPE_LATEST)`
    - DB 查询保留，改为调用 5 个 Gauge 的 `.set()` 方法
    - 多进程检测: `if "PROMETHEUS_MULTIPROC_DIR" in os.environ` → 创建 `CollectorRegistry(support_collectors_without_names=True)` + `MultiProcessCollector` + `generate_latest(registry)`；否则 `generate_latest()` 使用默认 REGISTRY
- **理由**:
  - `CONTENT_TYPE_LATEST`（`text/plain; version=0.0.4; charset=utf-8`）确保 Prometheus scraper 正确解析
  - 多进程模式下在请求上下文内创建新 `CollectorRegistry`（官方最佳实践，避免指标自动注册到 collector）

### 变更 5: 验证 `src/request_logging.py` 无需修改

- **文件**: `src/request_logging.py`（不修改）
- **验证**: 第 21 行 `from metrics import record_request` 和第 77 行 `record_request(path, start)` — 函数签名和行为保持一致

### 变更 6: 增强现有测试

- **文件**: `tests/test_production_hardening.py`
- **变更**: 增强 `test_metrics_includes_histogram` 测试，添加 Content-Type 验证和 gauge 类型验证
- **验证内容**:
  - `resp.headers["content-type"]` 等于 `CONTENT_TYPE_LATEST`
  - `# TYPE customer_service_conversations_total gauge` 出现在输出中

### 变更 7: 新建 prometheus 格式测试

- **文件**: `tests/test_metrics_prometheus.py`（新建）
- **测试内容**:
  - `test_metrics_content_type`: 验证 Content-Type 为 `CONTENT_TYPE_LATEST`
  - `test_metrics_includes_all_expected_metric_families`: 验证 histogram（HELP/TYPE/bucket/sum/count）+ 5 个 DB-count gauge 指标
  - `test_metrics_histogram_uses_standard_buckets`: 验证 bucket 边界 `0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, +Inf`

### 变更 8: 新建 ADR-0010

- **文件**: `docs/adr/0010-prometheus-metrics.md`（新建）
- **内容**: 记录从自定义 MetricsRegistry 迁移到 prometheus_client 的决策，包括多进程模式配置方法
- **多进程配置文档**:
  - 开发（单 worker）: 无需额外配置
  - 生产（多 worker）: 启动前设置 `PROMETHEUS_MULTIPROC_DIR` 环境变量并清空目录
  - gunicorn: 配置 `child_exit` 钩子调用 `multiprocess.mark_process_dead(worker.pid)`
  - uvicorn 多 worker: 确保 `PROMETHEUS_MULTIPROC_DIR` 在启动前设置

### 变更 9: 更新文档

- **文件**: `feature_list.json` — 添加 `prometheus-client-xdist` feature 条目
- **文件**: `progress.md` — 追加 P5 验证证据小节
- **文件**: `session-handoff.md` — 追加 P5 session block
- **文件**: `AGENTS.md` — Key Paths 表无需新增（metrics.py 已在表中），但 Definition Of Done 中的测试命令可更新为含 `-n auto`
- **文件**: `.env.example` — 添加 `PROMETHEUS_MULTIPROC_DIR` 环境变量文档

## 假设与决策

1. **先 xdist 后 prometheus_client**: xdist 不涉及业务代码修改，风险更低；完成后新测试也能更快运行
2. **`--dist=loadscope` 而非 `--dist=loadfile`**: 语义更精确（类优先于模块），是 pytest-xdist 官方对 `unittest.TestCase` 的推荐策略
3. **不修改任何测试文件或 conftest.py**: 每个 worker 是独立 OS 进程，全局状态自动隔离
4. **DB-count 指标从 counter 改为 gauge**: 反映当前 DB 状态而非单调递增，`multiprocess_mode='mostrecent'` 确保多进程聚合取最新值
5. **保留指标名含 `_total` 后缀**: 保证现有 Grafana dashboard/alerts 向后兼容。`prometheus_client.Gauge` 不强制 `_total` 后缀限制（仅 Counter 生效）
6. **不设 `addopts`**: 在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中设置 `addopts = "-n auto --dist=loadscope"` 会影响所有 pytest 调用（包括负载测试），因此保持命令行显式传递
7. **`prometheus_client` 多进程模块**: `from prometheus_client import multiprocess`，无需额外安装 `prometheus_multiprocess` 包
8. **`generate_latest()` 返回 bytes**: FastAPI `Response(content=data)` 接受 bytes，无需额外编码

## 实施顺序

```
变更 1 (添加依赖)
  ├→ 变更 2 (xdist: CI + init_check.py)
  │    └→ 验证: 现有 81 测试通过 -n auto --dist=loadscope
  │
  └→ 变更 3 (重写 metrics.py)
       ├→ 变更 4 (修改 /api/metrics 端点)
       ├→ 变更 5 (验证 request_logging.py 无需改)
       ├→ 变更 6 (增强现有测试)
       ├→ 变更 7 (新建 prometheus 格式测试)
       └→ 验证: 全部测试通过 + 覆盖率 ≥76%
            ├→ 变更 8 (ADR-0010)
            └→ 变更 9 (文档更新)
```

## 验证步骤

1. **依赖安装**: `uv sync` 成功安装 `prometheus_client` 和 `pytest-xdist`
2. **xdist 现有测试**: `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider -n auto --dist=loadscope -m "not load"` → 期望 81 passed，无新增失败
3. **prometheus 格式验证**: `.\.venv\Scripts\python.exe -m pytest tests\test_metrics_prometheus.py tests\test_production_hardening.py::ProductionHardeningTest::test_metrics_includes_histogram -v` → 期望全部通过
4. **完整测试套件**: `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider -n auto --dist=loadscope -m "not load" --cov --cov-report=term-missing` → 期望全部通过，覆盖率 ≥76%
5. **负载测试不受影响**: `.\.venv\Scripts\python.exe -m pytest tests\test_load_orchestrator.py -q -m load`（串行）→ 期望 4 passed
6. **Lint 检查**: `.\.venv\Scripts\ruff.exe check src tests` → 期望 All checks passed
7. **类型检查**: `.\.venv\Scripts\mypy.exe src` → 期望 Success: no issues
8. **Init 检查**: `.\init.cmd --check-only --skip-tests` → 期望 no failures
9. **Harness 验证**: `node scripts/harness/validate-harness.mjs` → 期望 100/100, no new warnings
10. **文档格式审查**: 目视检查 ADR-0010/session block/progress 小节格式与现有文档一致
