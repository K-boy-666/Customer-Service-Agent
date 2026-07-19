# ADR-0011: AI 驱动客服利润引擎架构

## 状态

已采纳 (2026-07-19)

## 背景

客服系统当前以「被动响应」为核心，存在三大瓶颈：

1. **响应效率瓶颈**：大促期间人工客服难以承接咨询洪峰，且现有 Orchestrator 只解决客户显性诉求，不识别潜在的销售机会。
2. **价值量化缺口**：客服贡献停留在"解决问题"层级，缺乏与营收增长的关联衡量手段；运营无法回答"客服对话贡献了多少 GMV"。
3. **跨平台数据碎片化**：Web / APP / 小程序 / 客服对话的用户身份与行为分散在多张表，无法形成统一用户画像与需求洞察。

现有架构（ADR-0001 / ADR-0002 / ADR-0004）只定义了 L0/L1/L2 三层 sub-agent（order-inquiry、consultation、after-sales、work-order、complaint、human-handoff），缺乏：

- 统一用户画像（多平台身份合并、意图标签、价值分层）
- 实时需求挖掘（交叉销售 / 向上销售机会识别）
- 主动推荐（基于画像与机会的个性化话术 + 漏斗追踪）
- 营收归因（多触点 ROI 与多模型对比）
- 峰值负载动态调度（降级保护 SLA）
- 智能人机协同升级（主动转人工 + 坐席辅助 + 负载均衡）
- 价值看板 API（KPI + 营收 + 洞察对外查询）

本 ADR 记录为补齐上述能力所作的关键架构决策，覆盖新增 L1 Agent、异步钩子机制、归因模型选择、会话上下文存储、峰值降级策略、主动转人工规则、价值看板 API 复用七个核心决策点。

## 决策

### 1. 新增两个 L1 Agent：recommendation-agent 与 analytics-agent

扩展 ADR-0004 子 Agent 三级权限模型，新增两个 L1 Agent：

| Agent | 权限级别 | 写入域 | 客户可见性 |
|-------|---------|--------|-----------|
| `recommendation-agent` | L1 | recommendation（推荐记录、漏斗事件） | 不直接回复客户，由 Orchestrator 决定是否呈现 |
| `analytics-agent` | L1 | attribution（归因记录、ROI 计算） | 不直接回复客户，归因结果仅供运营 / Orchestrator 内部使用 |

- L1 权限约束保持不变：仅创建内部记录，不直接面向客户。
- 通过 `src/security.py` 的 `ROLE_PERMISSIONS` 注册 `recommendation:write` 与 `analytics:write` 权限。
- 对应运行时模块 `src/recommendation_agent.py` 与 `src/analytics_agent.py`，封装 MCP 工具调用入口，复用 ADR-0002 通信协议（输出 `处理结果 + 客户回复[空] + 内部备注`）。
- Agent 系统提示位于 `.claude/agents/recommendation-agent.md` 与 `.claude/agents/analytics-agent.md`，头部声明权限级别。

### 2. 在 Orchestrator 主响应路径中嵌入异步钩子（ThreadPoolExecutor, max_workers=4）

- **需求挖掘**与**归因记录**通过 `concurrent.futures.ThreadPoolExecutor`（max_workers=4）异步执行，不阻塞客户响应。
- **推荐生成**是同步的（`opportunity_score > 0.6` 阈值触发），但有 2 秒超时保护（`Future.result(timeout=2.0)`），超时则返回空列表，主响应继续返回。
- 钩子模块 `src/profit_engine_hooks.py`，所有异常在 worker 内捕获并记录到 structlog，Future 永远不向上抛出。
- 每个钩子用独立 `database.session_scope()` 会话，与调用方会话解耦。
- 共享线程池懒加载且线程安全（`_executor_lock` 保护初始化）；测试通过 `shutdown_executor_for_tests(wait=True)` 清理。

**为何选择 ThreadPoolExecutor 而非 asyncio**：见 [备选方案](#备选方案) A。

### 3. 营收归因支持四种模型，默认 last_touch

在 `src/attribution_service.py` 中实现四种归因模型，常量 `ATTRIBUTION_MODELS = ("first_touch", "last_touch", "linear", "time_decay")`，`DEFAULT_MODEL = "last_touch"`：

| 模型 | 分配规则 | 适用场景 |
|------|---------|---------|
| `first_touch` | 100% 归属最早触点 | 评估「拉新」效果 |
| `last_touch` | 100% 归属最晚触点 | 评估「临门一脚」效果（默认） |
| `linear` | 触点均分 | 评估整链路贡献 |
| `time_decay` | 指数衰减，半衰期 7 天（`TIME_DECAY_HALF_LIFE_DAYS = 7`） | 越靠近转化的触点权重越高 |

- 归因窗口固定 24 小时（`ATTRIBUTION_WINDOW_HOURS = 24`）：只对 `order.created_at - 24h` 到 `order.created_at` 之间的 `TouchPoint` 做分配。
- 每个触点写一条 `AttributionRecord`，权重归一化到 `weight ∈ [0, 1]` 且所有触点权重之和为 1.0，确保 `sum(attributed_amount) == total_order_amount`。
- ROI 计算（`compute_roi`）：归因营收 / 客服成本（人力 ¥5/次 AgentAssistEvent + AI ¥0.1/次 CustomerServiceUsageEvent），含 Top-5 Agent 与 Top-5 话术排序。
- 多模型对比通过 `get_attribution_summary` 在看板端按相同时间窗聚合，避免重复计数订单。

**默认模型选择理由**：`last_touch` 是行业最保守、最易解释的模型，对运营审计与初期看板搭建最友好；其他模型可通过 `?model=` 参数切换。运营团队后续可在配置中覆盖默认值。

### 4. 会话上下文（挖掘结果）写入 `customer_service_usage_events.intents` JSON 字段（选项 B）

- 不新建独立表存储挖掘结果与会话上下文，而是把 `mining_result`、`recommendations`、`opportunity_score`、`intent_confidence` 序列化为 JSON 追加到最新 `CustomerServiceUsageEvent.intents` 字段。
- 该字段在 ADR-0005 已经是 JSON 字符串，本次扩展其 schema 而非变更表结构。
- 写入路径在 `src/orchestrator_api.py` 的 `respond_to_customer_message` 主流程末尾完成，与 usage event 的最终 flush 同事务，避免读到「半写入」状态。

**为何不新建独立表**：见 [备选方案](#备选方案) B。

### 5. 峰值降级策略：负载 > 80% 或队列等待 > 30s 时跳过 profit-engine 内部工作

- 在 `src/rate_limit.py` 中新增 `LoadMonitor`（线程安全，跟踪 in-flight 请求数 + 最近 1000 次等待时间的滑动窗口）。
- 阈值常量（env 可覆盖）：
  - `MAX_CONCURRENT_REQUESTS = 100`（`CS_MAX_CONCURRENT`）
  - `QUEUE_WAIT_THRESHOLD_SECONDS = 30`（`CS_QUEUE_WAIT_THRESHOLD`）
  - `LOAD_THRESHOLD_PERCENT = 80`（`CS_LOAD_THRESHOLD`）
- 降级策略模块 `src/degradation.py` 提供 `degradation_policy` 单例：
  - `should_skip_recommendation()`：降级时跳过推荐生成（最贵且非客户必需的工作）。
  - `should_use_profile_cache()`：降级时画像查询走缓存（短期可接受轻度过期）。
  - `should_delay_work_order()`：降级时非紧急工单延后（紧急投诉/升级永远不延后）。
  - `should_process(intent)`：**L0/L1/L2 业务 intent 永远处理**，仅 shed profit-engine 内部 intent（recommendation / analytics）。
- Prometheus 指标（`src/metrics.py` 新增）：`cs_queue_wait_seconds`（Histogram）、`cs_degradation_active`（Gauge 0/1）、`cs_active_requests`（Gauge）、`cs_load_percent`（Gauge）。

**为何不拒绝 L2 intent 在极端负载**：见 [备选方案](#备选方案) C。

### 6. 主动转人工触发条件：vip 用户 + intent_confidence < 0.7

- 触发规则在 `src/human_handoff_upgrade.py` 的 `should_proactively_handoff(user_value_tier, intent_confidence)` 中实现：
  - `user_value_tier == "vip"` AND `intent_confidence < 0.7` → 触发。
  - 非 vip 用户即使 confidence 很低也不主动转人工，走标准 reactive 转人工流程。
  - `intent_confidence` 缺失视为 0.0（"不知道客户要什么"时 vip 必转人工）。
- 触发后 `build_handoff_payload` 推送给坐席：
  - 用户 360° 画像（来自 `user_profile_service.get_profile`）
  - 最近 5 条推荐（来自 `recommendation_service`）
  - 最近 10 轮对话摘要（来自 `CustomerServiceUsageEvent`）
- 坐席辅助建议（`src/agent_assist_service.py`）在会话进入 handoff 状态后生成，每类最多 1 条：script（话术）/ knowledge（FAQ）/ cross_sell（交叉销售），可一键采纳并写入 `AgentAssistEvent` 用于归因。
- 坐席负载均衡（`src/agent_routing.py`）：按 skill 匹配 + 用户价值分层（vip 优先资深）+ 当前负载率 + agent_id 字典序 tie-breaker 路由。

### 7. 价值看板 API 复用现有 FastAPI app（在 `order_api.py` 追加端点），权限 `analytics:read`

在 `src/order_api.py` 末尾新增三个 v1 端点，复用现有 FastAPI app、限流装饰器、structlog 中间件、Prometheus 指标埋点：

| 端点 | 用途 | 限流 | 权限 |
|------|------|------|------|
| `GET /api/v1/profit-dashboard?start=...&end=...` | KPI + 营收 + 洞察三块数据，响应时间 < 2s | `LIMIT_READ` 120/min | `analytics:read` |
| `GET /api/v1/recommendations/funnel?start=...&end=...` | 各阶段事件数与转化率 | `LIMIT_READ` 120/min | `analytics:read` |
| `GET /api/v1/attributions?model=...&start=...&end=...` | 归因记录列表 + 多模型汇总 | `LIMIT_READ` 120/min | `analytics:read` |

- Prometheus 指标 `dashboard_latency_seconds`（Histogram，labels=endpoint）覆盖三个端点，bucket 边界 `(0.01, 0.1, 0.5, 1, 2, 5, 10)` 覆盖 2s SLA 与降级场景。
- `attribution_revenue_total` 指标用于看板实时展示归因营收总额。

## 理由

1. **复用 ADR-0004 L1 权限分层**：新增 recommendation / analytics agent 直接归入 L1，沿用"仅内部记录，不直接面向客户"的约束，避免引入新权限层级带来的认知与维护成本。
2. **ThreadPoolExecutor 优先 asyncio**：Orchestrator 主路径是同步代码，引入 asyncio 需在每个调用方（MCP / REST / direct tool）改造 event loop；ThreadPoolExecutor 与同步代码天然兼容，且 `.result(timeout=...)` 可在同步上下文中实现 SLA 保护。
3. **四种归因模型 + 默认 last_touch**：覆盖业界主流归因思路；默认 last_touch 是最保守、最易解释的模型，对初期看板搭建与运营审计最友好；半衰期 7 天与电商常见的"周末复购"周期对齐。
4. **会话上下文写入 JSON 字段（选项 B）**：避免新建表带来的 migration 复杂度；`intents` 字段已经是 JSON 字符串，扩展其 schema 是零成本演进。
5. **L0/L1/L2 业务 intent 永不 shed**：客服系统首要职责是回应客户，profit-engine 内部 intent（recommendation / analytics）是「锦上添花」可降级，业务 intent 是「雪中送炭」不可降级。
6. **vip + intent_confidence < 0.7 主动转人工**：vip 用户高价值、低置信度意味着 AI 不确定客户诉求，转人工是最安全的选择；非 vip 用户即使低置信度也走 reactive 流程，避免坐席负载被低价值用户占满。
7. **看板 API 复用 order_api.py**：避免新建独立 FastAPI app 带来的进程/部署/中间件重复配置成本；限流与权限装饰器天然复用。

## 后果

### 正面

- 客服可量化营收贡献（归因 + ROI 看板）
- 客服可主动推荐（画像 + 挖掘 + 推荐 + 漏斗闭环）
- 峰值负载可降级保护 SLA（profit-engine 内部工作可 shed，业务永不 shed）
- 坐席获得辅助（话术 / 知识 / 交叉销售建议 + 一键采纳 + 归因）
- vip 用户在 AI 不确定时优先转人工，提升高价值客户体验
- 运营可获得 Top Agent / 话术排序，驱动持续优化

### 负面

- **线程池开销**：异步钩子占用 max 4 workers，每个 worker 持有独立 DB session；在 SQLite 单写场景下需注意 WAL 配置（ADR-0010 已加固）。
- **归因模型选择影响 ROI 计算**：默认 last_touch 倾向"临门一脚"，可能低估早期触点贡献；运营团队需根据业务场景明确默认模型并文档化。
- **降级时推荐能力受限**：负载 > 80% 时推荐生成被跳过，客户在那段时间不会收到主动推荐，营收归因数据也会减少。
- **会话上下文 JSON 字段膨胀**：长期对话的 `intents` 字段会累积，需运营定期归档或后续迁移到独立表。
- **新增 9 张表 / 1 个迁移**（`0005_profit_engine_schema.py`）：SQLite 与 MySQL 双向兼容，幂等性由 `test_profit_engine_migration.py` 覆盖。

## 备选方案

### A. asyncio 异步钩子（被否）

**方案**：用 `asyncio.create_task` / `await asyncio.wait_for` 替代 ThreadPoolExecutor。

**否决理由**：
- Orchestrator 主路径（`orchestrator_runtime.py` / `orchestrator_mcp_tool.py` / `orchestrator_api.py`）是纯同步代码。
- 引入 asyncio 需在每个调用方（MCP server / REST API / 直接工具调用）改造 event loop，工作量与风险不成比例。
- ThreadPoolExecutor 的 `.result(timeout=...)` 可在同步上下文中实现 2s SLA 保护，无需嵌套 event loop。
- 现有 SQLite + SQLAlchemy 同步 session 与 asyncio 兼容性差，需引入 `aiosqlite` 与 `AsyncSession`，进一步放大改造范围。

### B. 会话上下文存储新建独立表（被否）

**方案**：新建 `conversation_context` 表，存储 `conversation_id` / `mining_result` / `recommendations` / `opportunity_score` / `intent_confidence`。

**否决理由**：
- 增加 migration 复杂度（新表 + 索引 + 外键约束 + 回滚脚本）。
- `customer_service_usage_events.intents` 字段（ADR-0005）已经是 JSON 字符串，扩展其 schema 是零成本演进。
- 上下文主要在「下一轮对话」与「坐席辅助」时被读取，按 `conversation_id` 查询最新一条 usage event 即可，独立表带来的查询性能收益有限。
- 后续若 JSON 字段膨胀或查询模式变化，仍可在不破坏现有 API 的前提下迁移到独立表。

### C. 拒绝 L2 intent 在极端负载（被否）

**方案**：负载极高时，拒绝 complaint / human_handoff 类 L2 intent，要求客户稍后重试。

**否决理由**：
- 投诉与转人工是客户在情绪或诉求无法被 AI 解决时的最后出口；拒绝会激化矛盾，存在舆情与监管风险。
- ADR-0003 三级情绪升级阶梯明确 L3（自伤 / 暴力 / 法律威胁）必须立即转人工、零安抚；拒绝 L2 intent 与 L3 责任冲突。
- 降级策略只针对 profit-engine 内部 intent（recommendation / analytics），这些是「锦上添花」工作，对客户体验无直接影响。

## References

- ADR-0001：单一 Orchestrator 入口
- ADR-0002：Agent 通信协议（输入 `客户上下文 + 任务`，输出 `处理结果 + 客户回复 + 内部备注`）
- ADR-0003：三级情绪升级阶梯
- ADR-0004：Sub-agent 三级权限模型（L0/L1/L2）
- ADR-0005：Scoped 验证与业务日 analytics
- ADR-0009：API 限流策略（slowapi）
- ADR-0010：Prometheus 标准指标迁移与 pytest-xdist 并行测试
- Spec: `.trae/specs/add-cs-profit-engine/spec.md`
- Tasks: `.trae/specs/add-cs-profit-engine/tasks.md`
- Checklist: `.trae/specs/add-cs-profit-engine/checklist.md`
