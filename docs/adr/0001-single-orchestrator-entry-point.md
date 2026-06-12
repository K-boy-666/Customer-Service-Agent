# ADR-0001: 单一 Orchestrator 作为客户入口

## 状态

已采纳 (2026-06-11)

## 背景

系统初始有两个 Agent 都定位为"客户第一触点"：
- `customer-service-orchestrator` — 全局编排器，覆盖完整对话生命周期
- `customer-service-dispatcher` — 中央调度器，负责意图识别和路由

两者在意图识别、路由分发、结果整合三项能力上重叠。

## 决策

**保留 `customer-service-orchestrator` 为系统唯一客户入口 Agent。**

`customer-service-dispatcher` 降级为 Orchestrator 的内部意图分析引擎，不直接面对客户。其职责收缩为：接收文本 → 输出结构化意图分类（含置信度和多意图拆解）。

## 理由

1. Orchestrator 覆盖完整对话生命周期（问候 → 信息确认 → 情绪安抚 → 敏感过滤 → 分发 → 整合 → 收尾 → 满意度），Dispatcher 只覆盖中间环节。
2. 单一入口简化子 Agent 通信拓扑——子 Agent 只需知道 Orchestrator，不需要判断"我该回复谁"。
3. Dispatcher 的核心能力（意图识别）仍被保留和利用，只是作为内部功能而非独立服务窗口。

## 后果

- Orchestrator 的 system prompt 需要更新：明确它是唯一入口，移除对 Dispatcher 作为独立入口的引用
- Dispatcher 的 system prompt 需要重写：移除"第一触点"定位，改为"内部引擎"
- 后续所有子 Agent 设计必须遵循"只与 Orchestrator 通信"的约束
