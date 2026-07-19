# AI 驱动客服利润引擎 Spec

## Why
当前客服系统以「被动响应」为核心，存在三大瓶颈：大促期间响应效率瓶颈（人工客服难以承接咨询洪峰）、客服价值难以量化（贡献停留在"解决问题"，缺乏与营收增长的关联衡量）、跨平台数据碎片化（各平台客服数据分散，无法形成统一用户画像与需求洞察）。需要将客服从「成本中心 / 响应中心」升级为「利润中心 / 增长中心」，通过整合智能接待、需求挖掘与价值转化，输出可落地的人机协同升级路径，实现从「被动响应」到「主动创收」的跃迁。

## What Changes
- 新增「统一用户画像服务」：聚合多平台（Web / APP / 小程序 / 客服对话）用户身份、行为、对话历史，输出 360° 用户视图（基础属性 + 意图标签 + 价值分层）
- 新增「需求挖掘引擎」：在现有 Orchestrator 中嵌入实时意图分析、交叉销售 / 向上销售机会识别（基于商品关系图谱与画像）
- 新增「主动推荐服务」：基于画像与挖掘机会，生成个性化推荐（商品 / 服务 / 优惠）+ 话术，并提供转化漏斗追踪（曝光→点击→咨询→下单）
- 新增「营收归因系统」：记录客服触点 → 订单转化路径，支持首次触点 / 末次触点 / 线性 / 时间衰减四种归因模型，提供 ROI 计算
- 升级「智能接待引擎」：扩展现有 Orchestrator，支持峰值负载动态调度、上下文预加载、智能路由优化
- 升级「人机协同机制」：扩展 human-handoff-agent，新增主动转人工触发规则、坐席辅助推荐（话术 / 知识 / 交叉销售）、负载均衡
- 新增「价值看板 API」：实时 KPI（响应时长 / 解决率 / CSAT）、营收指标（归因营收 / ROI / 转化率）、运营洞察（Top 机会 / 话术 / Agent）对外查询接口
- **BREAKING** 新增 L1 Agent：新增 `recommendation-agent`（L1，可生成推荐与漏斗事件）与 `analytics-agent`（L1，可写归因记录与画像更新事件），扩展 ADR-0004 权限分层目录

## Impact
- Affected specs: 客服 Orchestrator 路由协议、Sub-agent 权限分层（ADR-0004）、Agent 通信协议（ADR-0002）、峰值负载与限流（ADR-0009）
- Affected code:
  - `src/orchestrator_mcp_tool.py` - 嵌入需求挖掘钩子与归因事件触发
  - `src/orchestrator_runtime.py` / `src/orchestrator_api.py` - 升级调度策略与降级逻辑
  - `src/analytics_service.py` - 扩展为价值归因核心（新增归因 / ROI 计算）
  - `src/dispatcher.py` - 新增 recommendation / analytics 路由
  - `src/models.py` - 新增用户画像、推荐、归因、漏斗事件表
  - `src/rate_limit.py` - 增加全局负载感知与降级策略
  - `src/metrics.py` - 新增归因 / 推荐 / 转化指标埋点
  - `.claude/agents/` - 新增 `recommendation-agent.md`、`analytics-agent.md`
  - `alembic/versions/` - 新增迁移脚本 `0005_profit_engine_schema.py`
  - `tests/` - 新增对应单元 / 集成测试

## ADDED Requirements

### Requirement: 统一用户画像服务
系统 SHALL 提供统一用户画像服务（user_profile_service），聚合多平台（Web / APP / 小程序 / 客服对话）用户身份、行为、对话历史，输出 360° 用户视图，包含基础属性、近 30 天意图标签、价值分层（低 / 中 / 高 / 极高）。

#### Scenario: 多平台身份合并
- **WHEN** 同一用户在不同平台（手机号 / 邮箱 / open_id）发起咨询
- **THEN** 系统通过身份合并算法关联各平台行为，输出唯一 user_id 与聚合画像

#### Scenario: 实时画像更新
- **WHEN** 用户在客服对话中表达新需求（如咨询商品 A）
- **THEN** 画像服务在 5 秒内更新意图标签（如 `intent:product-A-inquiry`）与价值评分

#### Scenario: 价值分层
- **WHEN** 用户进入对话
- **THEN** 系统输出 `user_value_score`（基于 RFM + 客服互动加权）与对应分层（low / medium / high / vip）

### Requirement: 需求挖掘引擎
系统 SHALL 在 Orchestrator 处理用户消息时，并行运行需求挖掘（demand_mining_service），识别显性需求与潜在需求（交叉销售 / 向上销售机会），输出 intent、opportunity 列表与置信度。

#### Scenario: 潜在机会识别
- **WHEN** 用户咨询商品 A 的售后
- **THEN** 引擎基于用户画像与商品关系图谱（订单共现），识别商品 B 的交叉销售机会，输出 `opportunity_score`（0-1）与推荐理由

#### Scenario: 意图置信度
- **WHEN** 用户进入对话
- **THEN** 引擎输出 `intent_confidence`（0-1），低于 0.7 时触发主动转人工评估

#### Scenario: 异步非阻塞
- **WHEN** Orchestrator 处理用户消息
- **THEN** 需求挖掘以异步任务执行，不阻塞主响应路径；挖掘结果写入会话上下文供后续轮次使用

### Requirement: 主动推荐服务
系统 SHALL 提供主动推荐服务（recommendation_service），基于用户画像与挖掘机会，生成个性化推荐（商品 / 服务 / 优惠券）+ 话术，并追踪转化漏斗（exposure → click → consult → order）。

#### Scenario: 推荐生成
- **WHEN** 需求挖掘识别到交叉销售机会且 `opportunity_score > 0.6`
- **THEN** 推荐服务生成 ≤ 3 条推荐，每条含推荐内容、话术、预期转化率、推荐 ID

#### Scenario: 漏斗事件记录
- **WHEN** 推荐被曝光 / 点击 / 转化为咨询 / 转化为订单
- **THEN** 系统记录 `funnel_event`，关联 session_id、user_id、recommendation_id、event_type、timestamp

#### Scenario: 推荐去重
- **WHEN** 同一会话内同一推荐已曝光
- **THEN** 24 小时内不重复曝光同一推荐

### Requirement: 营收归因系统
系统 SHALL 提供多触点营收归因系统（attribution_service），记录客服对话 → 订单转化的完整路径，支持首次触点 / 末次触点 / 线性 / 时间衰减四种归因模型。

#### Scenario: 转化归因
- **WHEN** 用户在客服对话后 24 小时内完成订单
- **THEN** 系统按选定归因模型分配营收贡献，写入 `attribution_record`，关联 conversation_id、order_id、agent_id、recommendation_id

#### Scenario: ROI 计算
- **WHEN** 运营人员查询某时间段 ROI
- **THEN** 系统返回归因营收、客服成本（人力 + AI 算力）、ROI 比率、Top 增长 Agent / 话术

#### Scenario: 多模型对比
- **WHEN** 运营人员切换归因模型
- **THEN** 系统按新模型重算归因，并在看板展示模型间差异

### Requirement: 价值看板 API
系统 SHALL 提供价值看板 REST API，输出实时 KPI、营收指标、运营洞察三块数据，响应时间 < 2s。

#### Scenario: 看板查询
- **WHEN** 运营人员请求 `GET /api/v1/profit-dashboard?start=...&end=...`
- **THEN** 系统返回 JSON 看板数据，包含 KPI（响应时长、解决率、CSAT）、营收（归因营收、ROI、转化率）、洞察（Top 机会 / 话术 / Agent）

#### Scenario: 漏斗查询
- **WHEN** 运营人员请求 `GET /api/v1/recommendations/funnel?start=...&end=...`
- **THEN** 系统返回各阶段事件数与转化率

#### Scenario: 归因查询
- **WHEN** 运营人员请求 `GET /api/v1/attributions?model=last_touch&start=...&end=...`
- **THEN** 系统返回归因记录列表与汇总

### Requirement: 峰值负载动态调度
系统 SHALL 在大促等峰值场景下，动态调整 Orchestrator 调度策略，保证响应 SLA。

#### Scenario: 峰值降级
- **WHEN** 系统负载 > 80% 或队列等待 > 30s
- **THEN** 自动启用降级策略：L0 优先处理、推荐延迟生成、画像查询走缓存、非紧急工单延后

#### Scenario: 队列监控
- **WHEN** 队列等待时长超过阈值
- **THEN** 系统触发 Prometheus 告警，并在看板展示当前队列状态

### Requirement: 智能人机协同
系统 SHALL 升级人机协同机制，支持主动转人工触发、坐席辅助推荐、负载均衡。

#### Scenario: 主动转人工
- **WHEN** 用户价值评分 = vip 且 `intent_confidence < 0.7`
- **THEN** 系统主动触发转人工，并给坐席推送用户画像 + 推荐话术 + 历史对话摘要

#### Scenario: 坐席辅助
- **WHEN** 坐席处理人工会话
- **THEN** 系统实时推荐回复话术、相关知识、交叉销售机会，坐席可一键采纳并记录采纳事件

#### Scenario: 负载均衡
- **WHEN** 多坐席在线
- **THEN** 系统按当前负载与用户价值分层路由，vip 用户优先分配资深坐席

## MODIFIED Requirements

### Requirement: Sub-agent 权限分层（ADR-0004）
原有 L0 / L1 / L2 三层保留，新增两个 L1 Agent：
- `recommendation-agent`（L1）：可创建推荐记录与漏斗事件
- `analytics-agent`（L1）：可写归因记录、画像更新事件、ROI 计算结果

L1 权限约束（仅创建内部记录，不直接面向客户）保持不变；新增 Agent 不得直接向客户回复。

### Requirement: Orchestrator 路由协议
Orchestrator 在原有 Dispatcher 路由后，并行触发：
- 需求挖掘钩子（异步，不阻塞主响应）
- 主动推荐评估（`opportunity_score > 阈值` 时同步生成推荐，否则跳过）
- 营收归因事件记录（异步）

挖掘结果与会话上下文写入 `conversation_state`，供后续轮次与坐席辅助使用。

### Requirement: 限流与降级（ADR-0009）
`rate_limit.py` 在原 per-IP / per-user 限流基础上，新增全局负载感知：
- 监控全局并发数与队列长度
- 触发阈值时启用降级策略（详见「峰值负载动态调度」）
- 与 Prometheus 指标联动

## REMOVED Requirements
无（本变更以新增与扩展为主，不移除现有能力）。
