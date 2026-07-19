# Tasks

- [x] Task 1: 数据模型与迁移
  - [x] SubTask 1.1: 在 `src/models.py` 中新增用户画像表（`user_profile`、`user_identity`、`user_intent_tag`、`user_value_score`）
  - [x] SubTask 1.2: 在 `src/models.py` 中新增推荐与漏斗表（`recommendation`、`funnel_event`）
  - [x] SubTask 1.3: 在 `src/models.py` 中新增归因表（`attribution_record`、`touch_point`、`agent_assist_event`）
  - [x] SubTask 1.4: 编写 Alembic 迁移脚本 `alembic/versions/0005_profit_engine_schema.py`，确保 SQLite / MySQL 双向兼容
  - [x] SubTask 1.5: 编写迁移幂等性测试 `tests/test_profit_engine_migration.py`

- [x] Task 2: 统一用户画像服务
  - [x] SubTask 2.1: 新建 `src/user_profile_service.py`，提供 `get_profile` / `update_profile` / `merge_identity` / `update_intent_tag` 接口
  - [x] SubTask 2.2: 实现多平台身份合并逻辑（基于手机号 / 邮箱 / open_id 的优先级匹配）
  - [x] SubTask 2.3: 实现意图标签实时更新（订阅对话事件，5 秒内落库）
  - [x] SubTask 2.4: 实现价值评分算法（RFM 模型 + 客服互动加权 → low / medium / high / vip）
  - [x] SubTask 2.5: 编写 `tests/test_user_profile_service.py`，覆盖身份合并、画像更新、价值分层

- [x] Task 3: 需求挖掘引擎
  - [x] SubTask 3.1: 新建 `src/demand_mining_service.py`，输入对话上下文 + 画像，输出 intent、opportunity 列表、intent_confidence
  - [x] SubTask 3.2: 实现商品关系图谱查询接口（基于订单历史共现，输出关联商品与权重）
  - [x] SubTask 3.3: 实现交叉销售 / 向上销售机会评分算法（opportunity_score 0-1）
  - [x] SubTask 3.4: 编写 `tests/test_demand_mining_service.py`，含典型场景用例（售后 → 配件交叉销售、咨询 → 升级推荐）

- [x] Task 4: 主动推荐服务
  - [x] SubTask 4.1: 新建 `src/recommendation_service.py`，实现 `generate_recommendations`（≤ 3 条 / 含话术 / 预期转化率）
  - [x] SubTask 4.2: 实现转化漏斗事件记录（exposure / click / consult / order），含 24 小时去重逻辑
  - [x] SubTask 4.3: 新建 `.claude/agents/recommendation-agent.md` 与 `src/recommendation_agent.py`，封装推荐 MCP 工具（L1 权限）
  - [x] SubTask 4.4: 编写 `tests/test_recommendation_service.py`，覆盖推荐生成、去重、漏斗事件

- [x] Task 5: 营收归因系统
  - [x] SubTask 5.1: 新建 `src/attribution_service.py`，实现四种归因模型（first_touch / last_touch / linear / time_decay）
  - [x] SubTask 5.2: 实现订单事件订阅，自动写入 `attribution_record`（24 小时窗口）
  - [x] SubTask 5.3: 实现 ROI 计算逻辑（归因营收 / 客服成本，含 Top Agent / 话术排序）
  - [x] SubTask 5.4: 新建 `.claude/agents/analytics-agent.md` 与 `src/analytics_agent.py`，封装归因 MCP 工具（L1 权限）
  - [x] SubTask 5.5: 编写 `tests/test_attribution_service.py`，覆盖四种模型与多模型对比

- [x] Task 6: Orchestrator 集成
  - [x] SubTask 6.1: 在 `src/orchestrator_mcp_tool.py` 与 `src/orchestrator_api.py` 嵌入需求挖掘异步钩子
  - [x] SubTask 6.2: 实现 `opportunity_score > 0.6` 阈值触发推荐生成的同步逻辑
  - [x] SubTask 6.3: 实现营收归因事件异步记录（不阻塞主响应）
  - [x] SubTask 6.4: 升级 `src/dispatcher.py`，新增 recommendation / analytics 路由
  - [x] SubTask 6.5: 将挖掘结果与会话上下文写入 `conversation_state`
  - [x] SubTask 6.6: 编写 `tests/test_orchestrator_profit_integration.py`，验证异步钩子不阻塞主响应

- [x] Task 7: 峰值负载动态调度
  - [x] SubTask 7.1: 扩展 `src/rate_limit.py`，增加全局并发数与队列长度监控
  - [x] SubTask 7.2: 实现降级策略（L0 优先 / 推荐延迟 / 画像缓存 / 工单延后）
  - [x] SubTask 7.3: 新增 Prometheus 指标（`cs_queue_wait_seconds`、`cs_degradation_active`），更新 `src/metrics.py`
  - [x] SubTask 7.4: 编写 `tests/test_peak_load_degradation.py`，覆盖阈值触发与恢复

- [x] Task 8: 智能人机协同升级
  - [x] SubTask 8.1: 扩展 `.claude/agents/human-handoff-agent.md` 与对应 runtime，新增主动转人工触发规则（vip + intent_confidence < 0.7）
  - [x] SubTask 8.2: 实现坐席辅助推荐（话术 / 知识 / 交叉销售），含一键采纳与 `agent_assist_event` 记录
  - [x] SubTask 8.3: 实现坐席负载均衡路由（按负载 + 用户价值分层，vip 优先资深坐席）
  - [x] SubTask 8.4: 编写 `tests/test_human_handoff_upgrade.py`，覆盖主动转人工、坐席辅助、负载均衡

- [x] Task 9: 价值看板 API
  - [x] SubTask 9.1: 在 `src/orchestrator_api.py` 新增 `GET /api/v1/profit-dashboard` 端点（KPI + 营收 + 洞察）
  - [x] SubTask 9.2: 新增 `GET /api/v1/recommendations/funnel` 转化漏斗查询
  - [x] SubTask 9.3: 新增 `GET /api/v1/attributions` 归因查询（支持 model 参数切换）
  - [x] SubTask 9.4: 添加 Prometheus 指标埋点（dashboard_latency_seconds、attribution_revenue_total）
  - [x] SubTask 9.5: 编写 `tests/test_profit_dashboard_api.py`，覆盖三个端点与响应时间 < 2s

- [x] Task 10: 文档、ADR 与 Harness 同步
  - [x] SubTask 10.1: 撰写 `docs/adr/0011-cs-profit-engine.md`，记录架构决策（新增 L1 Agent、异步钩子、归因模型选择）
  - [x] SubTask 10.2: 更新 `feature_list.json`，新增 profit-engine 相关 feature 与依赖关系
  - [x] SubTask 10.3: 更新 `progress.md` 与 `session-handoff.md`，记录本次变更
  - [x] SubTask 10.4: 运行 `uv run pytest tests/ -q` 与 `./init.cmd --check-only --skip-tests` 验证 harness

# Task Dependencies
- Task 2、Task 3、Task 4、Task 5 均依赖 Task 1（数据模型先就位）
- Task 6 依赖 Task 2、Task 3、Task 4、Task 5（需要各服务可调用）
- Task 7 可与 Task 6 并行（不依赖具体服务，只依赖 rate_limit / metrics）
- Task 8 依赖 Task 6（需要画像 / 推荐 / 挖掘结果供坐席辅助）
- Task 9 依赖 Task 4、Task 5、Task 6（看板数据来源于推荐 / 归因 / Orchestrator）
- Task 10 在最后（汇总文档与 harness 验证）
