# ADR-0003: 三层情绪升级阶梯

## 状态

已采纳 (2026-06-11)

## 背景

系统中有三个 Agent 涉及客户情绪处理：
- Orchestrator 对所有客户做通用情绪安抚
- complaint-agent 对投诉客户做深度降级
- human-handoff-agent 在客户极度不满时转人工

如果不划清边界，会出现两个问题：(1) Orchestrator 对愤怒客户过度安抚、延迟转人工；(2) complaint-agent 和 human-handoff-agent 的升级触发条件重叠。

## 决策

采用**三层情绪升级阶梯**，按情绪强度 strict 分工：

| 级别 | 情绪强度 | 负责 Agent | 策略 |
|------|---------|-----------|------|
| L1 | Low / Medium | Orchestrator | 确认感受 → 转向解决问题 |
| L2 | High | complaint-agent | 深度共情 → 结构化记录 → 升级评估 |
| L3 | Critical | human-handoff-agent | 立即转人工，不拖延 |

**触发规则**：
- 关键词触发（"投诉/曝光/律师/监管/经理"）→ 直接 L2
- 情绪在 3 轮对话内从 L1 升级到 L2 → 立即路由到 complaint-agent
- 自伤/暴力威胁 → 直接 L3，不做任何安抚尝试

## 理由

1. **避免安抚陷阱**：让 Orchestrator 处理 L3 级情绪会让客户感觉被敷衍——危急情况需要立即行动而非共情话术。
2. **单一问责**：每个情绪级别只有一个 Agent 负责，不存在"我以为你会处理"的推诿空间。
3. **关键词触发是安全网**：即使模型漏判情绪强度，关键词也能确保法律/安全风险不被遗漏。

## 后果

- Orchestrator 必须在识别到 L2/L3 触发条件时立即路由，不能再做额外安抚尝试
- complaint-agent 和 human-handoff-agent 的升级触发条件需要对齐此阶梯
