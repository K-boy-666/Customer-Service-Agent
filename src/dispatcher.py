"""Intent dispatcher module interface for customer messages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


ORDER_ID_RE = re.compile(r"ORD-\d{8}-\d{3}", re.IGNORECASE)
TRACKING_RE = re.compile(r"\b[A-Z]{2}\d{10,16}\b", re.IGNORECASE)
RATING_RE = re.compile(r"([1-5])\s*(?:星|分|star|stars)", re.IGNORECASE)


@dataclass(frozen=True)
class DispatchIntent:
    intent: str
    confidence: float
    suggested_agent: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    fallback_reason: str = ""


@dataclass(frozen=True)
class DispatchResult:
    intents: list[DispatchIntent]
    safety_notes: list[str] = field(default_factory=list)


class RuleBasedIntentDispatcher:
    """Deterministic dispatcher adapter used by tests and local fallback."""

    l2_keywords = (
        "投诉",
        "曝光",
        "律师",
        "监管",
        "经理",
        "媒体",
        "315",
        "起诉",
        "法院",
        "complaint",
        "manager",
        "supervisor",
    )
    l3_keywords = ("自杀", "自残", "伤害自己", "杀人", "打死", "court order")
    after_sales_keywords = ("退货", "退款", "换货", "售后", "坏了", "故障", "不灵", "质量问题", "return", "refund", "exchange", "broken")
    order_keywords = ("订单", "物流", "快递", "发货", "到哪", "运单", "签收", "会员", "积分", "order", "shipment", "tracking")
    consultation_keywords = ("怎么", "如何", "规则", "政策", "多久", "多少天", "发票", "权益", "产品", "保修", "policy", "warranty")
    work_order_keywords = ("工单", "ticket", "进度", "派单")
    handoff_keywords = ("人工", "真人", "转人工", "human", "representative")
    injection_markers = ("ignore previous instructions", "忽略之前", "system prompt", "developer message")

    def analyze(self, message: str, context: dict[str, Any] | None = None) -> DispatchResult:
        context = context or {}
        text = message or ""
        intents: list[DispatchIntent] = []
        safety_notes = self._safety_notes(text)

        if self._contains_any(text, self.l3_keywords):
            return DispatchResult(
                [
                    DispatchIntent(
                        "human_handoff",
                        0.99,
                        "human-handoff-agent",
                        "Detected critical safety/legal handoff trigger.",
                        self._matches(text, self.l3_keywords),
                    )
                ],
                safety_notes,
            )

        rating = RATING_RE.search(text)
        if rating is not None:
            intents.append(DispatchIntent("satisfaction", 0.95, "customer-service-orchestrator", "Customer provided an explicit satisfaction rating.", [rating.group(0)]))

        l2_matches = self._matches(text, self.l2_keywords)
        if l2_matches:
            intents.append(DispatchIntent("complaint", 0.95, "complaint-agent", "Detected complaint/escalation keyword.", l2_matches))
            intents.append(DispatchIntent("work_order", 0.88, "work-order-agent", "Formal complaint should be recorded as a ticket.", l2_matches))

        if self._contains_any(text, self.handoff_keywords):
            intents.append(DispatchIntent("human_handoff", 0.9, "human-handoff-agent", "Customer requested a human handoff.", self._matches(text, self.handoff_keywords)))

        after_sales_matches = self._matches(text, self.after_sales_keywords)
        if after_sales_matches:
            intents.append(DispatchIntent("after_sales", 0.9, "after-sales-agent", "Detected return/refund/exchange/troubleshooting request.", after_sales_matches))

        order_matches = self._matches(text, self.order_keywords)
        order_id = context.get("order_id") or self._extract_order_id(text)
        if order_id or order_matches or TRACKING_RE.search(text):
            evidence = order_matches[:]
            if order_id:
                evidence.append(str(order_id))
            intents.append(DispatchIntent("order_inquiry", 0.86, "order-inquiry-agent", "Detected order, logistics, tracking, membership, or order ID signal.", evidence))

        work_matches = self._matches(text, self.work_order_keywords)
        if work_matches:
            intents.append(DispatchIntent("work_order", 0.82, "work-order-agent", "Detected work-order operation or ticket progress request.", work_matches))

        consult_matches = self._matches(text, self.consultation_keywords)
        if consult_matches:
            intents.append(DispatchIntent("consultation", 0.78, "consultation-agent", "Detected policy, FAQ, or product consultation request.", consult_matches))

        if not intents:
            intents.append(
                DispatchIntent(
                    "consultation",
                    0.45,
                    "consultation-agent",
                    "No high-confidence business intent; try knowledge-base assistance first.",
                    [],
                    "no_high_confidence_intent",
                )
            )

        return DispatchResult(self._dedupe_intents_by_priority(intents), safety_notes)

    def _safety_notes(self, message: str) -> list[str]:
        lower = message.lower()
        return [marker for marker in self.injection_markers if marker in lower]

    @staticmethod
    def _contains_any(message: str, keywords: tuple[str, ...]) -> bool:
        lower = message.lower()
        return any(keyword.lower() in lower for keyword in keywords)

    @classmethod
    def _matches(cls, message: str, keywords: tuple[str, ...]) -> list[str]:
        lower = message.lower()
        return [keyword for keyword in keywords if keyword.lower() in lower]

    @staticmethod
    def _extract_order_id(message: str) -> str | None:
        match = ORDER_ID_RE.search(message)
        return match.group(0).upper() if match else None

    @staticmethod
    def _dedupe_intents_by_priority(intents: list[DispatchIntent]) -> list[DispatchIntent]:
        priority = [
            "satisfaction",
            "complaint",
            "work_order",
            "human_handoff",
            "after_sales",
            "order_inquiry",
            "consultation",
        ]
        by_intent: dict[str, DispatchIntent] = {}
        for intent in intents:
            existing = by_intent.get(intent.intent)
            if existing is None or intent.confidence > existing.confidence:
                by_intent[intent.intent] = intent
        return sorted(by_intent.values(), key=lambda item: priority.index(item.intent))


class HybridIntentDispatcher:
    """Adapter seam for future LLM/RAG intent analysis with deterministic fallback."""

    def __init__(self, fallback: RuleBasedIntentDispatcher | None = None) -> None:
        self.fallback = fallback or RuleBasedIntentDispatcher()

    def analyze(self, message: str, context: dict[str, Any] | None = None) -> DispatchResult:
        result = self.fallback.analyze(message, context)
        intents = [
            DispatchIntent(
                intent.intent,
                intent.confidence,
                intent.suggested_agent,
                intent.reason,
                intent.evidence,
                intent.fallback_reason or "hybrid_adapter_used_rule_fallback",
            )
            for intent in result.intents
        ]
        return DispatchResult(intents, result.safety_notes)
