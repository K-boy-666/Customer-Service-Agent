# ADR-0010: Prometheus 标准指标迁移与 pytest-xdist 并行测试

## 状态

已采纳 (2026-07-01)

## 背景

P1 生产部署加固时搭建了 Prometheus + Grafana + Alertmanager 监控栈，但 `/api/metrics` 端点使用自定义 `MetricsRegistry` 手写拼接 Prometheus 文本格式，存在两个问题：

1. **非标准输出**: 虽然格式接近 Prometheus exposition format，但 Content-Type 头不是标准的 `text/plain; version=0.0.4; charset=utf-8`，可能导致某些 scraper 解析失败。
2. **无多进程支持**: 自定义 `MetricsRegistry` 是进程内的 dict，多 worker 部署时各 worker 的指标无法聚合。

此外，测试套件已增长到 81 个测试，串行运行约 63 秒，CI 反馈时间变长。

## 决策

### prometheus_client 迁移

采用 **prometheus_client 标准库 + 多进程模式** 方案：

- **指标定义** (`src/metrics.py`):
  - `Histogram("http_request_duration_seconds", ..., ["route"], buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))` — 指标名和 label 名与原始实现完全一致
  - 5 个 `Gauge` 类型 DB-count 指标：`CONVERSATIONS_TOTAL`、`HANDOFFS_TOTAL`、`TICKETS_TOTAL`、`RETURNS_TOTAL`、`SURVEYS_TOTAL`，均设置 `multiprocess_mode='mostrecent'`
  - 保留 `record_request(route, start_time)` 函数签名不变，`request_logging.py` 无需修改
- **端点改造** (`order_api.py`):
  - `/api/metrics` 返回 `Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)`
  - DB-count 指标在 scrape 时调用 `.set()` 写入当前 DB 值
  - 多进程检测: `if "PROMETHEUS_MULTIPROC_DIR" in os.environ` → 创建 `CollectorRegistry(support_collectors_without_names=True)` + `MultiProcessCollector`
- **指标类型变更**: `conversations` 和 `handoffs` 从 counter 改为 gauge（反映当前 DB 状态而非单调递增）。指标名保留 `_total` 后缀以兼容 Grafana dashboard/alerts

### 多进程模式配置

| 环境 | 配置 | 说明 |
|------|------|------|
| 开发（单 worker） | 无需配置 | `generate_latest()` 使用默认 REGISTRY |
| 生产（多 worker uvicorn） | 启动前设置 `PROMETHEUS_MULTIPROC_DIR` 环境变量 | 指向一个空目录，各 worker 写入指标文件 |
| gunicorn | 配置 `child_exit` 钩子 | `multiprocess.mark_process_dead(worker.pid)` 清理退出 worker 的指标文件 |

### pytest-xdist 并行测试

- **分发策略**: `--dist=loadscope`，按 `unittest.TestCase` 类分组，同一类所有方法在同一 worker 执行
- **CI 命令**: `uv run pytest tests/ -q -m "not load" -n auto --dist=loadscope --cov`
- **负载测试**: 保持串行（`load-test` job 不使用 xdist），确保 QPS/延迟数据准确
- **全局状态隔离**: 每个 worker 是独立 OS 进程，`DATABASE_URL`、`_engine`、`_CONVERSATION_STATES`、`_LOCAL_SEQ`、`limiter`、`REGISTRY` 均自动隔离，无需修改任何测试文件或 conftest.py

## 理由

1. **标准库替代自定义**: `prometheus_client` 是 Prometheus 官方 Python 客户端，支持标准 exposition format、多进程聚合、自动 metric family 注册
2. **多进程模式必要**: uvicorn `--workers N` 部署时各 worker 独立进程，`prometheus_client.multiprocess.MultiProcessCollector` 从共享目录聚合所有 worker 指标
3. **`generate_latest` 在请求上下文内创建 registry**: 官方最佳实践，避免指标自动注册到 collector
4. **`loadscope` 是官方推荐**: 对 `unittest.TestCase` 测试，按类分组是最安全的分发策略
5. **worker 进程隔离**: xdist worker 是独立 OS 进程（Windows 上使用 `spawn`），全局状态自动隔离，不需要 `worker_id` fixture 或 `tmp_path_factory`

## 后果

- `src/metrics.py` 完全重写，移除 `MetricsRegistry` 类和 `REGISTRY` 全局实例
- `src/order_api.py` 导入 5 个 Gauge 实例 + `prometheus_client` 函数，`/api/metrics` 端点改为 `generate_latest()` 输出
- `pyproject.toml` 新增 `prometheus_client>=0.20.0` 和 `pytest-xdist>=3.5`
- `.github/workflows/ci.yml` test job 添加 `-n auto --dist=loadscope`
- `scripts/harness/init_check.py` pytest 命令添加 `-n auto --dist=loadscope`
- `.env.example` 文档化 `PROMETHEUS_MULTIPROC_DIR`
- 测试从 ~63s 降至 ~13s（5x 加速），84 个测试通过，覆盖率 76.04%
- 新增 `tests/test_metrics_prometheus.py`（3 个测试验证 Content-Type、metric families、bucket 边界）
- 现有 `test_metrics_includes_histogram` 增强（验证 Content-Type 和 gauge 类型）
