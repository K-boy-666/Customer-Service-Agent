---
name: routing-order-inquiry-direct
description: 性能优化：Orchestrator 可直连 MCP 工具处理 L0 只读查询，无需调度子 Agent
metadata:
  type: feedback
---

## 性能优化规则

当客户的请求仅为 **L0 只读操作**（查订单/查物流/查账单/查会员）时，Orchestrator 应**直接使用 MCP 工具**处理，无需走调度子 Agent 的协议流程。Orchestrator 已具备所有 order-server MCP 工具的直接访问权限。

**Why:** 通过子 Agent 协议流程（情绪扫描 -> 内容审核 -> 意图分析 -> 格式化 -> 调度 -> 整合）会产生 30+ 次工具往返、耗时 140+ 秒。而 Orchestrator 直连 MCP 数据库，3-5 次调用即可完成。

**How to apply — Orchestrator 内部决策规则：**
- 若意图仅为查订单/查物流/查会员等 L0 只读 -> 直接使用 `mcp__order-server__*` 工具查询，整合结果后回复客户
- 若意图包含 L0 查询 + 其他需求（投诉、售后等）-> 对 L0 部分直连 MCP，对非 L0 部分调度对应子 Agent
- 若意图不含 L0 查询 -> 正常调度流程

**注意：** 此优化是 Orchestrator 内部性能决策，不可绕过 Orchestrator。所有客户交互必须经过 Orchestrator（ADR-0001）。
