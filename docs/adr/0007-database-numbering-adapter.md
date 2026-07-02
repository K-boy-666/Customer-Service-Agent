# ADR-0007: 数据库编号适配器与连接池

## 状态

已采纳 (2026-06-30)

## 背景

P1 生产部署加固发现三个数据层问题:

1. **多进程编号碰撞**:`service_layer.py` 的 `_next_number()` 使用进程内 `_NUMBER_SEQUENCES` dict + `threading.Lock` 生成编号(TK/RMA/SAT)。多进程部署(uvicorn --workers>1 或多容器)下,各进程独立计数,会生成重复编号并撞唯一约束崩溃。
2. **MySQL 连接池未调优**:`database.py` 的 `get_engine()` 仅设置 `pool_pre_ping=True`,未配置 `pool_recycle`。MySQL 默认 `wait_timeout=28800`(8h),空闲连接被服务端关闭后会导致 "MySQL has gone away"。
3. **启动迁移缺失**:lifespan 调用 `Base.metadata.create_all` 而非 alembic,生产容器不执行迁移。

## 决策

### 编号适配器

新增 `src/numbering.py`,定义 `NumberSequencer` 接口与两个实现:

- `InProcessSequencer`:SQLite/测试保留进程内锁 + DB 查询(单进程安全,向后兼容)
- `MysqlCounterSequencer`:MySQL 用 `sequence_counters` 表 + `LAST_INSERT_ID` 原子自增

MySQL 路径核心 SQL:
```sql
INSERT INTO sequence_counters (prefix_name, counter_date, last_value)
VALUES (:p, :d, 1)
ON DUPLICATE KEY UPDATE last_value = LAST_INSERT_ID(last_value + 1);
SELECT LAST_INSERT_ID();
```

`LAST_INSERT_ID(expr)` 将值写入连接级会话状态,后续 `SELECT LAST_INSERT_ID()` 返回该值且按连接隔离,无需 `FOR UPDATE` 行锁。

`database.py` 新增 `get_number_sequencer()` 工厂,依 URL scheme 返回实例。`service_layer.py` 三处编号调用改为使用 sequencer。

### 连接池配置

`get_engine()` 按方言分支:
- SQLite:`check_same_thread: False`
- MySQL:`pool_size=10`、`max_overflow=20`、`pool_recycle=3600`、`pool_timeout=30`,均 env 可配

`pool_recycle=3600` 远低于 MySQL `wait_timeout=28800`,避免空闲连接被关闭。

### 启动迁移

新增 `docker-entrypoint.sh`:容器启动先 `alembic upgrade head` 再起 uvicorn。lifespan 在生产环境跳过 `init_db()`/seed。

## 理由

1. **方言隔离**:不写 `if mysql` 散落业务代码,适配器模式隔离差异
2. **测试不受影响**:SQLite 路径保留原有逻辑,测试用 SQLite 约定不变
3. **连接级隔离**:LAST_INSERT_ID 是 MySQL 连接级状态,天然无锁无竞态
4. **唯一约束兜底**:ticket_number/return_number/survey_number 的 UniqueConstraint 保留为防御

## 后果

- 新增 `alembic/versions/0004_sequence_counters.py` 迁移
- `service_layer.py` 移除 `_next_number`、`_NUMBER_LOCKS`、`_NUMBER_SEQUENCES`(保留空占位)
- `database.py` 新增 `get_number_sequencer()` 和连接池参数
- `order_api.py` lifespan 新增生产分支
- 新增 `docker-entrypoint.sh` + Dockerfile ENTRYPOINT 改造
- 测试新增:sequencer 工厂选择、连接池配置、lifespan 生产跳过 seed
