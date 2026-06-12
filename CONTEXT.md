# CONTEXT.md — 客服智能体 2.0

## 核心概念

### Orchestrator（编排器）
系统的**唯一客户入口 Agent**（customer-service-orchestrator）。负责完整对话生命周期：问候 → 信息确认 → 情绪安抚 → 敏感过滤 → 意图分析 → 路由分发 → 结果整合 → 收尾 → 满意度调查。它是客户的第一触点也是最后触点。内部代号 "Xiao Ke (小客)"。

### Dispatcher（调度器）
Orchestrator 的**内部意图分析引擎**（customer-service-dispatcher）。不直接面对客户，接收 Orchestrator 传入的客户消息，输出结构化意图分类结果。职责边界仅限：意图识别、置信度评分、多意图拆解。

### 子 Agent（Sub-Agent）
由 Orchestrator 调度的专业化 Agent，各自负责一个业务领域。子 Agent 之间不互相通信，所有协调通过 Orchestrator 完成。

### 工单（Ticket）
正式记录和追踪客户问题的载体。由 ticket-system-operator 创建、查询、分配。工单有生命周期：新建 → 已分配 → 处理中 → 挂起 → 已解决 → 已关闭。

### 升级/转人工（Escalation / Human Handoff）
将客户对话从 AI Agent 转交给人类客服的流程。触发条件包括：客户明确要求、法律/合规风险、AI 3 次尝试未解决、安全威胁。

### 意图（Intent）
客户消息中表达的底层需求分类。当前六类意图及其对应的子 Agent：

| 意图 | 子 Agent | 职责 |
|------|---------|------|
| 查订单 | order-inquiry-agent | 只读查询：订单状态/物流/账单/会员 |
| 售后 | after-sales-agent | 退换货/退款处理/产品故障排查 |
| 咨询 | consultation-agent | 知识库检索：产品介绍/使用教程/活动规则/FAQ |
| 投诉 | complaint-agent | 投诉全生命周期：接收/记录/安抚/升级评估 |
| 工单 | work-order-agent | 工单系统操作：创建/查询/分配/追踪 |
| 转人工 | human-handoff-agent | 升级评估/队列管理/交接包准备 |

### 调度策略（Dispatch Strategy）
Orchestrator 决定并行或串行调度的判定规则。

**依赖测试**：如果任务 B 需要任务 A 的输出才能正确构建输入，则 A → B 串行。否则并行。多意图场景按优先级排序（投诉 > 售后 > 工单 > 查订单 > 咨询），优先级高的先执行，同级无依赖的并行。

### 情绪升级阶梯（Emotional Escalation Ladder）
三层情绪处理模型，定义情绪强度的上升路径和负责 Agent。

| 级别 | 情绪强度 | 负责 Agent | 策略 |
|------|---------|-----------|------|
| L1 | Low / Medium | Orchestrator | 确认感受 → 转向解决问题 |
| L2 | High（愤怒） | complaint-agent | 深度共情 → 结构化记录 → 升级评估 |
| L3 | Critical（威胁/法律/安全） | human-handoff-agent | 立即转人工 |

触发规则：客户消息含"投诉/曝光/律师/监管/经理"→ 直接 L2；3 轮内情绪升级 → L2；自伤/暴力威胁 → L3。

### 权限分级（Permission Tiers）
子 Agent 的三级操作权限模型。

| 级别 | 能力 | 适用 Agent |
|------|------|-----------|
| L0：只读 | 查询/检索/浏览，不可修改系统数据 | consultation-agent, order-inquiry-agent |
| L1：轻度操作 | L0 + 创建记录、发起流程、小额补偿 | after-sales-agent, work-order-agent |
| L2：无操作 | 仅对话，不接触业务系统 | complaint-agent, human-handoff-agent |

原则：Agent 只能降级不能升级。L0 Agent 永不能执行 L1 操作。

### 故障恢复（Error Recovery）
子 Agent 调用失败时的恢复策略。

| 故障类型 | 策略 |
|---------|------|
| 超时/无响应 | 重试 1 次（简化输入）。再失败 → 转人工 |
| 格式错误（缺`【客户回复】`） | 重试 1 次（附格式提示）。再失败 → Orchestrator 自行回答 |
| 信息矛盾（两 Agent 返回冲突） | 不仲裁，告知客户分歧，建议人工确认 |

核心原则：最多重试 1 次，避免客户等待过长。

### 满意度调查（Satisfaction Survey）
对话收尾阶段的客户评分流程。

**触发条件**：客户连续 2 轮未提新问题，且最后一轮含肯定信号（"谢谢/好的/解决了/没问题了"等）。

**评分处理**：4-5 星 → 感谢 + 邀请留言；1-3 星 → 道歉 + 问改进建议 + 创建"低分回访"工单（work-order-agent）+ 转人工主管跟进。

### 通信协议（Handoff Protocol）
Orchestrator 与子 Agent 之间的标准化消息格式。

**输入**（Orchestrator → 子 Agent）：`【客户上下文】` + `【任务】` 两个区块，包含客户ID、订单号、消息原文、情绪强度、相关历史。

**输出**（子 Agent → Orchestrator）：`【处理结果】`（success/partial/failed/needs-escalation）+ `【客户回复】`（可直接发给客户的自然语言）+ `【内部备注】`（不给客户看的内部信息）。
