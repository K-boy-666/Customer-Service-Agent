# P2: 多代理并发与负载测试

## 摘要

验证编排器在并行客户会话下的行为正确性与性能。研究发现 3 个真实并发 bug 需修复,然后建立三层并发测试体系(L1 集成层/L2 API 层/L3 负载层)。

## 当前状态分析

### 已确认的并发风险

| 风险 | 位置 | 严重度 | 说明 |
|------|------|--------|------|
| A. `_CONVERSATION_STATES` 线程不安全 | `orchestrator_runtime.py:80,937-984` | 高危 | 模块级 `OrderedDict` 无锁,`move_to_end`/`popitem` 非原子,多线程竞态触发 `RuntimeError`/`KeyError`;`_remember_conversation_state` 的 get+set read-modify-write 丢更新 |
| B. SQLite 无 WAL/busy_timeout | `database.py:42-57` | 中危 | 默认 rollback journal,并发写立即抛 `database is locked` |
| C. 幂等键 TOCTOU 竞态 | `security.py:208-237` | 高危 | 先查后插无原子保护,并发同 key 双插入触发 `IntegrityError` 回滚,违反幂等语义 |

### 架构关键事实

- `handle_message` 是同步方法,每请求创建独立 `CustomerServiceOrchestrator` 但共享全局 `_CONVERSATION_STATES`
- `database.session_scope()` 每次创建独立 Session,SQLite `check_same_thread=False` 已设置
- `POST /api/orchestrator/respond` 是同步端点,FastAPI 在 AnyIO 线程池执行
- `numbering.py` 的 `InProcessSequencer` 已用 `threading.Lock` 保护(正确性 OK,锁内做 DB 查询是性能瓶颈)
- 项目无 `pytest-asyncio`,并发测试以 `ThreadPoolExecutor` 为主,不引入新依赖

## 代码改动

### 改动 1:`_CONVERSATION_STATES` 加 RLock(修复风险 A)

**文件**:`src/orchestrator_runtime.py`

- 新增 `import threading`
- 第 80 行下方新增 `_STATES_LOCK = threading.RLock()`(RLock 因 `_remember_conversation_state` 读后写需可重入)
- 重构 `_get_conversation_state`:double-checked locking —— 锁内仅做字典操作,DB 查询移出锁外,查到后回锁内写缓存
- 重构 `_remember_conversation_state`:read-modify-write + LRU 驱逐包在锁内,DB 持久化保持在锁外
- 新增 `def reset_conversation_states_for_tests()` 供测试调用

### 改动 2:SQLite 启用 WAL + busy_timeout(修复风险 B)

**文件**:`src/database.py`

- 顶部新增 `from sqlalchemy import event`
- `get_engine` 的 SQLite 分支:创建 engine 后注册 `@event.listens_for(engine, "connect")` 钩子,对每个新连接执行:
  - `PRAGMA journal_mode=WAL`
  - `PRAGMA busy_timeout=5000`
  - `PRAGMA synchronous=NORMAL`
- MySQL 分支不受影响
- 现有 tearDown 已清理 `-wal`/`-shm` 文件,无需改动

### 改动 3:`run_idempotent` 处理并发 IntegrityError(修复风险 C)

**文件**:`src/security.py`

- 顶部新增 `from sqlalchemy.exc import IntegrityError`
- `run_idempotent` 中 `session.add` + `session.flush()` 包裹 `try/except IntegrityError`:
  - 捕获后 `session.rollback()`,重新查询返回缓存响应
  - 业务写入(ticket/return)在同一 session 内 flush,回滚时一并回滚 —— 这是正确的,因为并发赢家已写入等价记录

### 改动 4:测试基础设施

**文件**:`tests/conftest.py`(新建)、`pyproject.toml`

- `conftest.py` 提供共享 fixture:`temp_db`、`seeded_db`、`verification_token`、`actor`,减少并发测试样板
- `pyproject.toml` 的 `[tool.pytest.ini_options]` 注册 markers:
  ```toml
  markers = [
    "slow: 较慢的并发测试,可用 -m 'not slow' 跳过",
    "load: 负载吞吐测试,默认不在门禁中运行",
  ]
  ```

## 测试文件结构

```
tests/
  conftest.py                              [新增] 共享 fixture
  test_concurrency_isolation.py            [新增] 会话状态隔离 + _CONVERSATION_STATES 线程安全
  test_concurrency_numbering.py            [新增] 跨类型并发编号无碰撞
  test_concurrency_idempotency.py          [新增] 幂等键并发去重 + IntegrityError 路径
  test_concurrency_sqlite_wal.py           [新增] SQLite WAL 并发读写不死锁
  test_load_orchestrator.py                [新增] 负载响应时间/P95/吞吐 (mark=load)
scripts/
  loadtest/
    load_orchestrator.py                   [新增] L3 独立负载脚本 (手动运行)
```

## 测试用例清单

### `test_concurrency_isolation.py`(L1+L2,会话隔离)

| 用例 | 验证点 |
|------|--------|
| `test_conversation_states_isolated_across_concurrent_sessions` | N 线程各持唯一 conversation_id 并发调用,断言 follow-up 的 order_id 不串台 |
| `test_same_conversation_followup_preserves_context_under_concurrency` | 同一 conversation_id 并发 follow-up,断言最终状态一致(不丢更新) |
| `test_conversation_states_lru_eviction_safe_under_pressure` | 并发写入 >256 个会话,断言不抛异常且长度 ≤ 256 |
| `test_conversation_state_cache_hit_does_not_block_other_threads` | 缓存命中线程 + DB 查询线程并行,断言无死锁 |
| `test_api_endpoint_concurrent_isolation_via_per_thread_testclient` | L2:4 线程各持独立 TestClient 并发 POST,断言状态隔离 |

### `test_concurrency_numbering.py`(L1,编号无碰撞)

| 用例 | 验证点 |
|------|--------|
| `test_concurrent_ticket_numbers_unique_high_contention` | 16 线程 × 8 次 = 128 次 create_ticket,全唯一 |
| `test_concurrent_return_numbers_unique` | 并发 create_return,RMA 编号全唯一 |
| `test_concurrent_survey_numbers_unique` | 并发 submit_satisfaction,编号全唯一 |
| `test_concurrent_mixed_type_numbers_no_cross_collision` | 三类同时跑,同类型无碰撞且互不干扰 |
| `test_local_seq_survives_db_reset` | reset_for_tests 后并发创建,编号从 DB max+1 继续 |

### `test_concurrency_idempotency.py`(L1,幂等去重)

| 用例 | 验证点 |
|------|--------|
| `test_concurrent_duplicate_idempotency_key_dedupes` | 8 线程相同 key+payload 并发,只创建 1 条,响应一致 |
| `test_concurrent_same_key_different_payload_raises_conflict` | 同 key 不同 payload 并发,至少一个返回 409 |
| `test_idempotency_replay_after_business_write_rollback` | 并发失败方业务写入回滚,最终 DB 仅 1 条记录 |
| `test_orchestrator_write_fanout_idempotency_keys_no_collision` | fan-out 场景并发重放,派生 key 不碰撞 |

### `test_concurrency_sqlite_wal.py`(L1,WAL 并发)

| 用例 | 验证点 |
|------|--------|
| `test_wal_mode_enabled_on_test_database` | 断言 `journal_mode` = `wal` |
| `test_concurrent_writes_no_database_locked` | 8 线程 × 10 次写,无 SQLITE_BUSY |
| `test_concurrent_read_during_write_not_blocked` | 写+读并行,读不被阻塞 |
| `test_wal_checkpoint_safe_under_load` | 持续写入触发 checkpoint,无死锁 |

### `test_load_orchestrator.py`(mark=load,负载)

| 用例 | 验证点 |
|------|--------|
| `test_p95_response_time_under_mixed_load` | 50 并发混合请求,P95 < 800ms |
| `test_throughput_sustained_burst` | 10 秒压测,QPS ≥ 基线,无内存泄漏 |
| `test_tail_latency_under_write_contention` | 100 并发纯写,最大响应时间 < 阈值 |
| `test_no_state_leak_between_load_waves` | 两轮压测间无累积泄漏 |

### `scripts/loadtest/load_orchestrator.py`(L3,手动)

uvicorn + httpx.AsyncClient,输出 QPS/P50/P95/P99/错误率。不进 pytest 门禁。

## 假设与决策

- **不引入新依赖**:基于 `threading`/`concurrent.futures` + 已有 `httpx`,不引入 locust/pytest-asyncio
- **RLock 而非 Lock**:`_remember_conversation_state` 内部读后写需可重入
- **DB I/O 移出锁外**:double-checked locking,极端并发下可能重复 DB 查询(幂等,可接受),换取吞吐
- **L1/L2 进 pytest 门禁,L3 手动运行**:符合项目"pytest 门禁 + 手动验证证据"双轨惯例
- **WAL 仅影响 SQLite**:MySQL 分支不进入,生产不受影响
- **IntegrityError 回滚业务写入是正确的**:并发赢家已写入等价记录,输家不应重复

## 验证步骤

1. `.\.venv\Scripts\ruff.exe check src tests` + `ruff format --check` 全过
2. `.\.venv\Scripts\mypy.exe src` → 0 errors
3. `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider` 全量通过(含新增并发测试)
4. `.\.venv\Scripts\python.exe -m pytest tests\test_load_orchestrator.py -q -m load` 负载测试通过(可选)
5. `.\init.cmd --check-only --skip-tests` 无失败
6. `node scripts/harness/validate-harness.mjs` 无新警告
7. 更新 `feature_list.json`(`load-testing` 移入 features,status=done)、`progress.md`、`session-handoff.md`
