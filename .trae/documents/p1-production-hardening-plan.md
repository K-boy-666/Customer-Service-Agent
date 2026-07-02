# P1 生产部署加固实施计划 — 客服智能体 2.0

> 创建于 2026-06-30。覆盖 MySQL 迁移、密钥管理、监控、CI/CD 四个领域。

## 摘要

本计划将 `feature_list.json` 中的 P1 `production-deployment` 从 planned 推进为 done。基于 2026-06-27 已完成的加固基线(配置校验、健康/就绪/指标端点、Dockerfile、docker-compose MySQL 8.4、Alembic 显式迁移、备份脚本),补齐四个关键差距:多进程序列号不安全、MySQL 连接池未调优、无 CI/CD 流水线、无监控栈。遵循 docker-compose 部署模型,不引入 K8s/服务网格。

## 现状分析

### 已有基线(2026-06-27)

- `src/config.py`:生产配置校验(APP_ENV=production 阻止 dev OTP / 默认 JWT / 缺失 JWKS / SQLite)
- `src/order_api.py`:`/api/health`、`/api/ready`、`/api/metrics`(Prometheus 文本格式)、`/api/v2/*` JSON 写端点
- `.env.example`:全部配置项已文档化
- `.claude/mcp.json`:已移除静态凭据
- `Dockerfile` + `docker-compose.yml`:MySQL 8.4 + utf8mb4,但 compose APP_ENV=development
- `alembic/versions/0001-0003`:显式迁移
- `scripts/backup_mysql.py`:mysqldump + gzip 备份
- `tests/test_production_hardening.py`:配置校验、并发编号唯一性、对话状态持久化测试
- `.github/workflows/`:仅 DeepSeek 代码审查,无构建/测试/部署流水线

### 新发现的关键风险(基线之外)

1. **启动迁移缺失**:`order_api.py` lifespan 调用 `database.init_db()`(即 `Base.metadata.create_all`)而非 alembic,Docker CMD 直接跑 uvicorn —— 生产容器内从不执行迁移,也不写 `alembic_version` 表。后续再跑 `alembic upgrade head` 会因表已存在而失败。
2. **种子数据污染**:lifespan 在 `is_db_empty()` 为真时调用 `seed_data.seed()` —— 全新生产库会被灌入开发种子数据。
3. **多进程编号碰撞**:`_next_number` 的进程内 `_NUMBER_SEQUENCES` + `threading.Lock` 仅保护单进程,多进程(uvicorn --workers>1 或多容器)必撞唯一约束并崩溃(无重试)。

## 拟议变更

### 领域 1:MySQL 迁移加固

#### 1.1 编号序列多进程安全

**新增文件**:
- `src/numbering.py`:序列适配器(`InProcessSequencer` for SQLite,`MysqlCounterSequencer` for MySQL)
- `alembic/versions/0004_sequence_counters.py`:创建 `sequence_counters` 表(复合主键 prefix_name + counter_date)

**修改文件**:
- `src/service_layer.py`:三处 `_next_number()` 调用改为 `database.get_number_sequencer().next_number()`
- `src/database.py`:新增 `get_number_sequencer()` 工厂,依 URL scheme 返回实例

**核心决策**:MySQL 走 `INSERT...ON DUPLICATE KEY UPDATE last_value=LAST_INSERT_ID(last_value+1)` 原子自增,`SELECT LAST_INSERT_ID()` 按连接隔离,天然无锁无竞态。SQLite 路径保留现有进程内锁,测试不受影响。唯一约束保留为最终兜底。

迁移 0004 创建计数器表:
```python
op.create_table(
    "sequence_counters",
    sa.Column("prefix_name", sa.String(length=10), nullable=False),
    sa.Column("counter_date", sa.String(length=8), nullable=False),
    sa.Column("last_value", sa.Integer(), nullable=False, default=0),
    sa.UniqueConstraint("prefix_name", "counter_date", name="uq_seq_prefix_date"),
)
```

适配器核心:
```python
class MysqlCounterSequencer(NumberSequencer):
    def next_number(self, session, column, prefix_name, lock_key):
        today = datetime.now().strftime("%Y%m%d")
        prefix = f"{prefix_name}-{today}-"
        session.execute(text(
            "INSERT INTO sequence_counters (prefix_name, counter_date, last_value) "
            "VALUES (:p, :d, 1) "
            "ON DUPLICATE KEY UPDATE last_value = LAST_INSERT_ID(last_value + 1)"
        ), {"p": prefix_name, "d": today})
        seq = session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        return f"{prefix}{seq:03d}"
```

#### 1.2 连接池配置

**修改文件**:`src/database.py`

`get_engine()` 按方言分支配置连接池:
- SQLite:保持 `check_same_thread: False`
- MySQL:`pool_size=10`(env `DB_POOL_SIZE`)、`max_overflow=20`(env `DB_MAX_OVERFLOW`)、`pool_recycle=3600`(env `DB_POOL_RECYCLE`)、`pool_timeout=30`(env `DB_POOL_TIMEOUT`)

`pool_recycle=3600` 远低于 MySQL 默认 `wait_timeout=28800`(8h),杜绝 "MySQL has gone away"。`pool_pre_ping=True` 已有(悲观断连检测)。

#### 1.3 生产 compose overlay

**新增文件**:`docker-compose.prod.yml`

通过 `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` 叠加。关键差异:
- `APP_ENV: production`
- `DATABASE_URL` 使用 `${MYSQL_PASSWORD}` 而非硬编码
- 敏感项支持 `_FILE` 后缀(读 Docker secret 文件)
- `DB_POOL_*` 环境变量
- `restart: unless-stopped` + 资源限制
- `secrets` 段引用外部 secret 文件

dev compose(`docker-compose.yml`)保持不动。

#### 1.4 Alembic MySQL 8.4 兼容性验证

**已核验**:JSON 列用 `sa.JSON()`(MySQL 8.4 原生 JSON 支持);String 均显式 length;Float→MySQL DOUBLE;无裸 String 无长度隐患。`0002` 用独立 MetaData + checkfirst,up→down→up 安全。

**验证手段**:CI MySQL 8.4 service 容器执行 up→down→up→seed→表断言(见领域 4)。

#### 1.5 启动迁移与种子污染修复

**修改文件**:`src/order_api.py`、`Dockerfile`
**新增文件**:`docker-entrypoint.sh`

`order_api.py` lifespan 改造:
- 生产环境:跳过 `init_db()`/seed,schema 由 entrypoint 的 `alembic upgrade head` 负责
- 开发环境:保持现有 `init_db()` + seed 逻辑

`docker-entrypoint.sh`:
```sh
#!/bin/sh
set -e
alembic upgrade head
exec uv run uvicorn order_api:app --host 0.0.0.0 --port 8000
```

`Dockerfile`:增加 `COPY docker-entrypoint.sh` + `ENTRYPOINT` + 非 root `USER app`。

### 领域 2:密钥管理

#### 2.1 文件式 secret 支持

**修改文件**:`src/config.py`、`src/database.py`、`.env.example`

引入 `_FILE` 后缀读取助手(标准 Docker secret 模式):
```python
def _read_secret(name: str, environ: Mapping[str, str]) -> str:
    file_path = environ.get(f"{name}_FILE")
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return environ.get(name, "")
```

`AUTH_DEV_SECRET` 改用 `_read_secret`;`DATABASE_URL` 中的密码支持 `DB_PASSWORD_FILE`。增量兼容:`_FILE` 未设时回退 env。

#### 2.2 密钥轮换策略

**新增文件**:`docs/adr/0006-secrets-and-deployment.md`
**修改文件**:`docs/production-hardening.md`

轮换 runbook:
- JWT/OIDC 签名密钥:JWKS 双 key 并存期 ≥ token TTL,本项仅消费 JWKS 无需重启
- AUTH_DEV_SECRET:滚动重启所有实例,低峰期操作(OTP TTL 默认 10 分钟)
- MySQL 密码:双用户 CREATE→更新 secret→滚动重启→DROP 旧用户
- 备份加密:gz 产物落地加密卷

**决策**:env 优先 + `_FILE` 增强 + 文档化轮换,不引入 Vault/Sealed Secrets(匹配 compose 部署规模)。

### 领域 3:监控

#### 3.1 指标增强(直方图 + 延迟)

**新增文件**:`src/metrics.py`
**修改文件**:`src/order_api.py`

轻量线程安全 `MetricsRegistry`(零新依赖):
- 请求延迟直方图(buckets: 0.05/0.1/0.25/0.5/1.0/2.5/5.0/10.0/+inf)
- `@app.middleware("http")` 记录 `time.perf_counter()` 差值
- `/api/metrics` 追加 `http_request_duration_seconds_bucket/sum/count`

**已知取舍**:现有 DB 计数指标是 gauge 语义却标 counter(每次 scrape 重查 DB)。本次不重构既有指标,仅增量加直方图;ADR 记录为已知项,后续可迁移到 `prometheus_client`。

#### 3.2 Prometheus + Grafana + Alertmanager 栈

**新增文件**:
- `docker-compose.monitoring.yml`(用 `--profile monitoring` 启用)
- `monitoring/prometheus.yml`(scrape `/api/metrics`,15s 间隔)
- `monitoring/alerts.yml`(ApiDown/HighHandoffRate/HighApiLatencyP95)
- `monitoring/grafana/provisioning/datasources/prometheus.yml`
- `monitoring/grafana/provisioning/dashboards/customer-service.json`

告警规则(最小集):
- `ApiDown`:scrape 失败 2 分钟 → critical
- `HighHandoffRate`:人工转接率 > 30% 持续 15 分钟 → warning
- `HighApiLatencyP95`:P95 延迟 > 2s 持续 10 分钟 → warning

### 领域 4:CI/CD

#### 4.1 CI 测试 + Lint + 迁移冒烟

**新增文件**:`.github/workflows/ci.yml`
**修改文件**:`pyproject.toml`(dev 依赖 + ruff/mypy 配置)

`pyproject.toml` 新增:
```toml
[dependency-groups]
dev = ["pytest>=9.1.1", "ruff>=0.9", "mypy>=1.13"]

[tool.ruff]
line-length = 120
target-version = "py310"
src = ["src"]
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.10"
ignore_missing_imports = true
files = ["src"]
```

CI 三个 job:
1. **lint**:ruff check + ruff format --check + mypy src(ubuntu-latest,setup-uv 缓存)
2. **test**:Python 3.10/3.11/3.12 矩阵,uv sync → alembic upgrade head(SQLite)→ pytest
3. **migration-smoke**:MySQL 8.4 service 容器,up→down→up→seed→表断言(不跑完整 pytest,测试约定 SQLite)

setup-uv 缓存:`enable-cache: true` + `cache-dependency-glob: "**/uv.lock"`。

**lint 启动策略**:ruff 首次引入可能报存量问题,单独 PR 跑 `ruff check --fix` + `ruff format` 一次性规整。mypy 宽松起步,存量类型问题列入后续清理。

#### 4.2 CD 构建推送

**新增文件**:`.github/workflows/release.yml`

main/tag 触发,build-push 到 GHCR(latest+sha+tag)。使用 docker/build-push-action@v6 + GHA 缓存。

部署文档化为:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.monitoring.yml --profile monitoring pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.monitoring.yml --profile monitoring up -d
```

entrypoint 内 `alembic upgrade head` 自动完成迁移。

#### 4.3 迁移自动化

由 `docker-entrypoint.sh` 覆盖:容器启动先 `alembic upgrade head` 再起 uvicorn。多实例部署依赖 alembic 幂等(`alembic_version` 已是 head 时立即退出)。若后续多实例竞争成为问题,再加一次性 migration 容器。

### 领域 5:Harness 治理更新

按 AGENTS.md Definition of Done 同步:
1. `feature_list.json`:production-deployment 移入 features,设 status=done
2. `progress.md`:追加完成块 + 验证证据
3. `session-handoff.md`:追加 Session 块
4. 新增 `docs/adr/0006-secrets-and-deployment.md`、`docs/adr/0007-database-numbering-adapter.md`
5. `docs/production-hardening.md`:补充 prod compose 启动、监控栈启用、轮换 runbook、entrypoint 说明
6. `scripts/harness/init_check.py`:可选扩展(生产跳过 seed)

## 假设与决策

| 决策 | 理由 |
|------|------|
| 编号序列用计数器表 + LAST_INSERT_ID | 连接级隔离,无锁无竞态;SQLite 路径不变 |
| 连接池 pool_recycle=3600 | 远低于 MySQL wait_timeout=28800,避免 "MySQL has gone away" |
| 生产 compose 用 override 叠加 | dev compose 不动,标准 compose 多文件模式 |
| 启动迁移用 entrypoint 而非 lifespan | 生产由 alembic 独占 schema,避免 create_all 冲突 |
| 密钥用 env + _FILE 增强 | 增量兼容,不破坏现有 env-only 部署 |
| 监控用 Prometheus + Grafana | /api/metrics 已输出 Prometheus 文本,自然 fit |
| CI 用 GitHub Actions + setup-uv | 已有 GH 工作流,setup-uv 缓存成熟 |
| CD 仅构建推送 GHCR | 匹配 compose 部署模型,不引入未使用的部署器 |
| MySQL job 不跑完整 pytest | 测试约定 SQLite,仅做迁移冒烟 |

## 实施顺序

按依赖关系排序(每步可独立验证):

1. **基础数据层**(无依赖):`numbering.py` + 迁移 0004 + service_layer/database 改造 + 池配置 + 测试
2. **启动修复**(依赖 1):order_api lifespan 生产分支 + docker-entrypoint.sh + Dockerfile
3. **密钥**(依赖 2):config/database `_FILE` 支持 + prod compose + 轮换 runbook + ADR-0006
4. **监控**(依赖 1-2):metrics.py + 中间件 + monitoring compose + prometheus/grafana 配置 + 测试
5. **CI**(依赖 1-2):pyproject.toml dev 依赖 + ruff/mypy 规整 + ci.yml
6. **CD**(依赖 5):release.yml + GHCR
7. **Harness 收尾**(依赖 1-6):feature_list/progress/handoff/ADR 文档同步 + 全量验证

## 验证步骤

- `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider`:全量通过(含新增 sequencer/pool/metrics/lifespan 测试)
- `.\init.cmd --check-only --skip-tests`:无 fail
- `node scripts/harness/validate-harness.mjs`:无新 warning
- `docker compose -f docker-compose.yml -f docker-compose.prod.yml config`:配置校验通过
- CI workflow 在 GitHub 上绿(lint + test 矩阵 + migration-smoke)
- 新增测试:
  - `test_sequencer_factory_selects_by_url`:mysql URL 返回 MysqlCounterSequencer,sqlite 返回 InProcessSequencer
  - `test_engine_pool_config`:mysql engine pool.size()==10,_max_overflow==20
  - `test_lifespan_skips_seed_in_production`:APP_ENV=production 不调用 seed_data.seed
  - `test_metrics_includes_histogram`:/api/metrics 含 http_request_duration_seconds_bucket

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| ruff/mypy 首次引入大量存量告警 | 单独 PR 跑 ruff check --fix + ruff format;mypy 宽松起步 |
| MySQL 真实序列行为无法在 SQLite 测试复现 | 适配器类型选择可测;MySQL 真实路径由 CI migration-smoke 覆盖 |
| 现有 gauge-as-counter 指标语义不纯 | 本次不重构,仅增量加直方图;ADR 记录为已知项 |
| 多实例并发首次迁移竞争 | 依赖 alembic 幂等;若实际出现再加一次性 migration 容器 |
| Dockerfile 非 root 用户影响卷写权限 | entrypoint 确保目录属主;CI build 验证 |
| Windows 本地 uv 受阻 | CI 跑 ubuntu;本地验证用 .venv 直跑 |

## 关键文件清单

**新增**(14 个):
- `src/numbering.py`、`src/metrics.py`
- `alembic/versions/0004_sequence_counters.py`
- `docker-compose.prod.yml`、`docker-compose.monitoring.yml`
- `monitoring/prometheus.yml`、`monitoring/alerts.yml`
- `monitoring/grafana/provisioning/datasources/prometheus.yml`
- `monitoring/grafana/provisioning/dashboards/customer-service.json`
- `docker-entrypoint.sh`
- `.github/workflows/ci.yml`、`.github/workflows/release.yml`
- `docs/adr/0006-secrets-and-deployment.md`、`docs/adr/0007-database-numbering-adapter.md`

**修改**(11 个):
- `src/database.py`、`src/service_layer.py`、`src/config.py`、`src/order_api.py`
- `Dockerfile`、`pyproject.toml`、`.env.example`
- `tests/test_production_hardening.py`
- `docs/production-hardening.md`
- `feature_list.json`、`progress.md`、`session-handoff.md`
