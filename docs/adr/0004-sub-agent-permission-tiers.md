# ADR-0004: 子 Agent 三级权限模型

## 状态

已采纳 (2026-06-11)

## 背景

各子 Agent 的权限定义分散在各自的 system prompt 中（"你可以 X，不能 Y"），没有统一模型。这导致两个风险：
1. 新 Agent 创建时权限不受约束，可能被赋予超出其职责的操作能力
2. 现有 Agent 的 prompt 漂移后，权限边界模糊

## 决策

采用**三级权限模型**，每个子 Agent 归类到唯一级别：

| 级别 | 标识 | 可执行操作 | 适用 Agent |
|------|------|-----------|-----------|
| L0 | 只读 | 查询/检索/浏览，不可修改任何系统数据 | consultation-agent, order-inquiry-agent |
| L1 | 轻度操作 | L0 + 创建记录、发起流程、小额补偿（有上限） | after-sales-agent, work-order-agent |
| L2 | 无操作 | 仅对话，不接触任何业务系统 | complaint-agent, human-handoff-agent |

**约束**：Agent 只能降级不能升级——L0 Agent 不可执行 L1 操作，需要时由 Orchestrator 调度 L1 Agent。

## 理由

1. **最小权限原则**：只给每个 Agent 完成其职责所需的最小权限。complaint-agent 只记录投诉——它不需要也绝不能执行退款。
2. **可审计**：操作权集中在 L1 Agent，任何数据修改都通过已知的两个 Agent，便于追踪。
3. **防止 prompt 注入**：即使客户通过对话诱导 Agent，L2 Agent 也无系统可操作。

## 后果

- 每个子 Agent 的 system prompt 必须在头部声明其权限级别
- Orchestrator 不得将需要 L1 操作的任务分发给 L0 或 L2 Agent
