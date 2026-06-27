"""Deterministic runtime for the customer-service orchestrator.

The prompt files describe how the orchestrator should behave. This module turns
that contract into ordinary Python code so the routing, tool use, and response
composition can be tested without launching a live LLM agent.
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import database
import service_layer as svc
from kb_service import FaqRetrievalService, get_faq_retriever
from security import Actor, Verification, run_idempotent
from starlette.exceptions import HTTPException


LOGGER = logging.getLogger(__name__)
MAX_CONVERSATION_STATES = 256

ORDER_ID_RE = re.compile(r"ORD-\d{8}-\d{3}", re.IGNORECASE)
TRACKING_RE = re.compile(r"\b[A-Z]{2}\d{10,16}\b", re.IGNORECASE)
RATING_RE = re.compile(r"([1-5])\s*(?:星|分|star|stars)", re.IGNORECASE)

L2_KEYWORDS = ("投诉", "曝光", "律师", "监管", "经理", "媒体", "315", "起诉", "法院")
L3_KEYWORDS = ("自杀", "自残", "伤害自己", "杀了", "打死", "炸", "法院传票", "court order")
AFTER_SALES_KEYWORDS = (
    "\u9000\u8d27",
    "\u9000\u6b3e",
    "\u6362\u8d27",
    "\u552e\u540e",
    "\u574f\u4e86",
    "\u6545\u969c",
    "\u4e0d\u7075",
    "\u8d28\u91cf\u95ee\u9898",
)
ORDER_KEYWORDS = ("订单", "物流", "快递", "发货", "到哪", "运单", "签收", "会员", "积分")
CONSULTATION_KEYWORDS = (
    "\u600e\u4e48",
    "\u5982\u4f55",
    "\u89c4\u5219",
    "\u653f\u7b56",
    "\u591a\u4e45",
    "\u591a\u5c11\u5929",
    "\u53d1\u7968",
    "\u6743\u76ca",
    "\u4ea7\u54c1",
    "\u4fdd\u4fee",
)
WORK_ORDER_KEYWORDS = ("工单", "ticket", "进度", "派单")


@dataclass
class CustomerContext:
    message: str
    customer_id: int | None = None
    order_id: str | None = None
    conversation_id: str | None = None


@dataclass
class ConversationState:
    customer_id: int | None = None
    order_id: str | None = None
    updated_at: str = ""


_CONVERSATION_STATES: OrderedDict[str, ConversationState] = OrderedDict()


@dataclass
class IntentAnalysis:
    intent: str
    confidence: float
    suggested_agent: str
    reason: str


@dataclass
class ToolCall:
    tool: str
    status: str
    summary: str


@dataclass
class AgentResult:
    agent: str
    status: str
    customer_reply: str
    internal_notes: str

    def protocol_output(self) -> str:
        return (
            "【处理结果】\n"
            f"状态: {self.status}\n\n"
            "【客户回复】\n"
            f"{self.customer_reply}\n\n"
            "【内部备注】\n"
            f"{self.internal_notes}"
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["protocol_output"] = self.protocol_output()
        return data


@dataclass
class OrchestratorRun:
    status: str
    conversation_id: str
    customer_reply: str
    emotional_level: str
    intent_analysis: list[IntentAnalysis]
    dispatched_agents: list[str]
    agent_results: list[AgentResult]
    tool_calls: list[ToolCall] = field(default_factory=list)
    needs_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "conversation_id": self.conversation_id,
            "customer_reply": self.customer_reply,
            "final_response": self.customer_reply,
            "emotional_level": self.emotional_level,
            "intent_analysis": [asdict(i) for i in self.intent_analysis],
            "dispatched_agents": self.dispatched_agents,
            "agent_results": [r.to_dict() for r in self.agent_results],
            "tool_calls": [asdict(c) for c in self.tool_calls],
            "needs_human": self.needs_human,
        }


class LocalCustomerServiceTools:
    """Local tool adapter backed by service-layer security checks."""

    def __init__(
        self,
        faq_path: str | Path | None = None,
        faq_retriever: FaqRetrievalService | None = None,
        root_actor: Actor | None = None,
        verification: Verification | None = None,
        idempotency_key: str = "",
        request_id: str = "",
    ):
        self.faq_path = Path(faq_path or Path(__file__).parent.parent / "data" / "faq.json")
        self._faq: list[dict[str, Any]] | None = None
        self._faq_retriever = faq_retriever
        self.root_actor = root_actor or Actor("orchestrator-runtime", "orchestrator", {})
        self.verification = verification
        self.idempotency_key = idempotency_key
        self.request_id = request_id

    def _load_faq(self) -> list[dict[str, Any]]:
        if self._faq is None:
            with open(self.faq_path, "r", encoding="utf-8") as f:
                self._faq = json.load(f)
        return self._faq

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        if self.verification is None:
            raise HTTPException(status_code=401, detail="missing_identity_verification")
        with database.session_scope() as session:
            return svc.get_order(session, self._agent("order_inquiry"), order_id, self.verification)

    def search_orders(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        with database.session_scope() as session:
            return svc.search_orders(session, self._agent("order_inquiry"), query, limit)["data"]

    def get_shipment(self, order_id: str) -> dict[str, Any] | None:
        if self.verification is None:
            raise HTTPException(status_code=401, detail="missing_identity_verification")
        with database.session_scope() as session:
            return svc.get_shipment(session, self._agent("order_inquiry"), order_id, self.verification)

    def track_by_number(self, tracking_number: str) -> dict[str, Any] | None:
        with database.session_scope() as session:
            return svc.track_by_number(session, self._agent("order_inquiry"), tracking_number)

    def get_customer(self, customer_id: int) -> dict[str, Any] | None:
        if self.verification is None:
            raise HTTPException(status_code=401, detail="missing_identity_verification")
        with database.session_scope() as session:
            return svc.get_customer(session, self._agent("order_inquiry"), customer_id, self.verification)

    def search_faq(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        return self._get_faq_retriever().search(query, limit=limit)

    def _get_faq_retriever(self) -> FaqRetrievalService:
        if self._faq_retriever is None:
            self._faq_retriever = get_faq_retriever(str(self.faq_path))
        return self._faq_retriever

    def _scoped_idempotency_key(self, operation: str, payload: dict[str, Any], auto_key: str) -> str:
        if not self.idempotency_key:
            return auto_key
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        return f"{self.idempotency_key}:{operation}:{digest}"

    def create_ticket(
        self,
        title: str,
        description: str,
        ticket_type: str = "incident",
        priority: str = "P3",
        customer_id: int | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        if self.verification is None:
            raise HTTPException(status_code=401, detail="missing_identity_verification")
        payload = {"title": title, "description": description, "ticket_type": ticket_type, "priority": priority, "customer_id": customer_id, "order_id": order_id}
        operation_key = self._scoped_idempotency_key("ticket", payload, f"auto-ticket-{title}-{customer_id}-{order_id}")
        with database.session_scope() as session:
            response, _code, _replayed = run_idempotent(
                session,
                self._agent("work_order"),
                "orchestrator:create_ticket",
                operation_key,
                payload,
                lambda: (
                    svc.create_ticket(
                        session,
                        self._agent("work_order"),
                        title,
                        description,
                        ticket_type,
                        priority,
                        customer_id,
                        order_id,
                        self.verification,
                        operation_key,
                        self.request_id,
                    ),
                    201,
                ),
            )
            return response

    def create_return(
        self,
        order_id: str,
        reason: str,
        return_type: str,
        description: str,
        customer_id: int | None = None,
    ) -> dict[str, Any] | None:
        if self.verification is None:
            raise HTTPException(status_code=401, detail="missing_identity_verification")
        payload = {"order_id": order_id, "return_type": return_type, "reason": reason, "description": description, "customer_id": customer_id}
        operation_key = self._scoped_idempotency_key("return", payload, f"auto-return-{order_id}-{return_type}")
        with database.session_scope() as session:
            response, _code, _replayed = run_idempotent(
                session,
                self._agent("after_sales"),
                "orchestrator:create_return",
                operation_key,
                payload,
                lambda: (
                    svc.create_return(
                        session,
                        self._agent("after_sales"),
                        order_id,
                        return_type,
                        reason,
                        description,
                        customer_id,
                        self.verification,
                        operation_key,
                        self.request_id,
                    ),
                    201,
                ),
            )
            return response

    def submit_satisfaction(
        self,
        rating: int,
        feedback: str,
        customer_id: int | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        if self.verification is None:
            raise HTTPException(status_code=401, detail="missing_identity_verification")
        payload = {"rating": rating, "feedback": feedback, "customer_id": customer_id, "order_id": order_id}
        operation_key = self._scoped_idempotency_key("survey", payload, f"auto-survey-{customer_id}-{order_id}-{rating}")
        with database.session_scope() as session:
            response, _code, _replayed = run_idempotent(
                session,
                self._agent("work_order"),
                "orchestrator:submit_satisfaction",
                operation_key,
                payload,
                lambda: (
                    svc.submit_survey(
                        session,
                        self._agent("work_order"),
                        rating,
                        feedback,
                        customer_id,
                        order_id,
                        self.verification,
                        operation_key,
                        self.request_id,
                    ),
                    201,
                ),
            )
            return response

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def _agent(self, role: str) -> Actor:
        return Actor(subject=f"{self.root_actor.subject}:{role}", role=role, claims=self.root_actor.claims)


class CustomerServiceOrchestrator:
    """Executable orchestrator that mirrors ADR-0001/0002 routing rules."""

    def __init__(
        self,
        tools: LocalCustomerServiceTools | None = None,
        faq_retriever: FaqRetrievalService | None = None,
        actor: Actor | None = None,
        verification: Verification | None = None,
        idempotency_key: str = "",
        request_id: str = "",
    ):
        self.faq_retriever = faq_retriever
        self.tools = tools or LocalCustomerServiceTools(
            faq_retriever=faq_retriever,
            root_actor=actor,
            verification=verification,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        self.tool_calls: list[ToolCall] = []

    def handle_message(
        self,
        message: str,
        customer_id: int | None = None,
        order_id: str | None = None,
        conversation_id: str | None = None,
        actor: Actor | None = None,
        verification: Verification | None = None,
        idempotency_key: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        if actor or verification or idempotency_key or request_id:
            self.tools = LocalCustomerServiceTools(
                faq_retriever=self.faq_retriever,
                root_actor=actor,
                verification=verification,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        extracted_order_id = order_id or self._extract_order_id(message)
        state = self._get_conversation_state(conversation_id)
        context = CustomerContext(
            message=message.strip(),
            customer_id=customer_id if customer_id is not None else state.customer_id,
            order_id=extracted_order_id or state.order_id,
            conversation_id=conversation_id or f"conv-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        )
        self.tool_calls = []
        emotional_level = "L1"
        intents: list[IntentAnalysis] = []
        results: list[AgentResult] = []

        try:
            if not context.message:
                run = OrchestratorRun(
                    status="needs-info",
                    conversation_id=context.conversation_id or "",
                    customer_reply="您好，我是小客。请告诉我您需要查询或处理的问题，我会马上帮您。",
                    emotional_level=emotional_level,
                    intent_analysis=[],
                    dispatched_agents=[],
                    agent_results=[],
                    tool_calls=[],
                )
                result = run.to_dict()
                self._record_usage_event(result, context)
                return result

            emotional_level = self._classify_emotion(context.message)
            intents = self.analyze_intents(context, emotional_level)

            for intent in intents:
                handler = self._handler_for(intent.intent)
                if handler is None:
                    continue
                handler_results = handler(context)
                if isinstance(handler_results, list):
                    results.extend(handler_results)
                else:
                    results.append(handler_results)

                if intent.intent == "human_handoff":
                    break

            dispatched_agents = self._unique([result.agent for result in results])
            customer_reply = self._compose_reply(results)
            needs_human = any(result.status == "needs-escalation" for result in results)
            status = self._overall_status(results, needs_human)

            run = OrchestratorRun(
                status=status,
                conversation_id=context.conversation_id or "",
                customer_reply=customer_reply,
                emotional_level=emotional_level,
                intent_analysis=intents,
                dispatched_agents=dispatched_agents,
                agent_results=results,
                tool_calls=self.tool_calls,
                needs_human=needs_human,
            )
            result = run.to_dict()
            self._remember_conversation_state(context)
            self._record_usage_event(result, context)
            return result
        except Exception as exc:
            run = OrchestratorRun(
                status="failed",
                conversation_id=context.conversation_id or "",
                customer_reply="",
                emotional_level=emotional_level,
                intent_analysis=intents,
                dispatched_agents=self._unique([result.agent for result in results]),
                agent_results=results,
                tool_calls=self.tool_calls,
                needs_human=False,
            )
            self._remember_conversation_state(context)
            self._record_usage_event(run.to_dict(), context, failure_reason=str(exc))
            raise

    def analyze_intents(
        self, context: CustomerContext, emotional_level: str
    ) -> list[IntentAnalysis]:
        text = context.message
        intents: list[IntentAnalysis] = []

        if emotional_level == "L3":
            return [
                IntentAnalysis(
                    "human_handoff",
                    0.99,
                    "human-handoff-agent",
                    "Detected critical safety/legal handoff trigger.",
                )
            ]

        rating = self._extract_rating(text)
        if rating is not None:
            intents.append(
                IntentAnalysis(
                    "satisfaction",
                    0.95,
                    "customer-service-orchestrator",
                    "Customer provided an explicit satisfaction rating.",
                )
            )

        if self._contains_any(text, L2_KEYWORDS):
            intents.append(
                IntentAnalysis(
                    "complaint",
                    0.95,
                    "complaint-agent",
                    "Detected complaint/escalation keyword.",
                )
            )
            intents.append(
                IntentAnalysis(
                    "work_order",
                    0.88,
                    "work-order-agent",
                    "Formal complaint should be recorded as a ticket.",
                )
            )

        if self._contains_any(text, AFTER_SALES_KEYWORDS):
            intents.append(
                IntentAnalysis(
                    "after_sales",
                    0.9,
                    "after-sales-agent",
                    "Detected return/refund/exchange/troubleshooting request.",
                )
            )

        if context.order_id or self._contains_any(text, ORDER_KEYWORDS) or TRACKING_RE.search(text):
            intents.append(
                IntentAnalysis(
                    "order_inquiry",
                    0.86,
                    "order-inquiry-agent",
                    "Detected order, logistics, tracking, membership, or order ID signal.",
                )
            )

        if self._contains_any(text, WORK_ORDER_KEYWORDS):
            intents.append(
                IntentAnalysis(
                    "work_order",
                    0.82,
                    "work-order-agent",
                    "Detected work-order operation or ticket progress request.",
                )
            )

        if self._contains_any(text, CONSULTATION_KEYWORDS):
            intents.append(
                IntentAnalysis(
                    "consultation",
                    0.78,
                    "consultation-agent",
                    "Detected policy, FAQ, or product consultation request.",
                )
            )

        if not intents:
            intents.append(
                IntentAnalysis(
                    "consultation",
                    0.45,
                    "consultation-agent",
                    "No high-confidence business intent; try knowledge-base assistance first.",
                )
            )

        return self._dedupe_intents_by_priority(intents)

    def _handle_order_inquiry(self, context: CustomerContext) -> AgentResult:
        text = context.message
        tracking = self._extract_tracking_number(text)

        if "会员" in text or "积分" in text:
            if context.customer_id is None:
                return AgentResult(
                    "order-inquiry-agent",
                    "partial",
                    "我可以帮您查询会员等级和积分，请您补充会员ID或注册手机号后我再继续核对。",
                    "Missing customer identifier for membership lookup.",
                )
            customer = self._call_tool("get_customer", self.tools.get_customer, context.customer_id)
            if not customer:
                return AgentResult(
                    "order-inquiry-agent",
                    "failed",
                    "暂时没有查到对应会员信息，请您核对会员ID或稍后转人工处理。",
                    f"Customer {context.customer_id} not found.",
                )
            return AgentResult(
                "order-inquiry-agent",
                "success",
                (
                    f"查到了，您当前是{customer['membership_tier']}会员，"
                    f"积分余额为{customer['points']}分，历史订单数"
                    f"{customer['order_summary']['total_orders']}笔。"
                ),
                f"Customer lookup succeeded for customer_id={context.customer_id}.",
            )

        if tracking:
            shipment = self._call_tool("track_by_number", self.tools.track_by_number, tracking)
            if shipment:
                return self._shipment_result(shipment, f"tracking_number={tracking}")
            return AgentResult(
                "order-inquiry-agent",
                "failed",
                f"没有查到运单号 {tracking} 的物流记录，请您核对后再发我一次。",
                "Tracking number lookup returned no shipment.",
            )

        if not context.order_id:
            return AgentResult(
                "order-inquiry-agent",
                "partial",
                "我可以帮您查订单和物流，请您提供订单号，格式类似 ORD-20260621-001。",
                "Missing order_id for order inquiry.",
            )

        order = self._call_tool("get_order", self.tools.get_order, context.order_id)
        if order is None:
            return AgentResult(
                "order-inquiry-agent",
                "failed",
                f"没有查到订单 {context.order_id}，请您核对订单号后再发我一次。",
                f"Order {context.order_id} not found.",
            )

        wants_shipment = self._contains_any(text, ("物流", "快递", "到哪", "发货", "签收"))
        if wants_shipment:
            try:
                shipment = self._call_tool("get_shipment", self.tools.get_shipment, context.order_id)
            except HTTPException as exc:
                detail = str(getattr(exc, "detail", exc)).lower()
                if exc.status_code == 404 and "shipment" in detail:
                    item_names = "、".join(item["name"] for item in order["items"][:3])
                    return AgentResult(
                        "order-inquiry-agent",
                        "partial",
                        (
                            f"订单 {order['id']} 当前状态是 {order['status']}，"
                            f"商品包括 {item_names}。目前没有物流记录，可能尚未发货、订单已取消，"
                            "或该订单不需要物流。我会把已经处理的事项一并记录。"
                        ),
                        f"Order lookup succeeded for order_id={context.order_id}; no shipment record was available.",
                    )
                raise
            if shipment:
                return self._shipment_result(shipment, f"order_id={context.order_id}", order)

        item_names = "、".join(item["name"] for item in order["items"][:3])
        return AgentResult(
            "order-inquiry-agent",
            "success",
            (
                f"订单 {order['id']} 当前状态是 {order['status']}，"
                f"商品包括 {item_names}，订单金额 {order['total_amount']} {order['currency']}。"
            ),
            f"Order lookup succeeded for order_id={context.order_id}.",
        )

    def _handle_after_sales(self, context: CustomerContext) -> AgentResult:
        if not context.order_id:
            return AgentResult(
                "after-sales-agent",
                "partial",
                "可以的，我先帮您处理售后。请您提供对应订单号，我会继续核对并发起申请。",
                "Missing order_id for after-sales request.",
            )

        return_type = "refund" if "退款" in context.message or "仅退款" in context.message else "return"
        if "换货" in context.message:
            return_type = "exchange"

        reason = self._short_reason(context.message)
        created = self._call_tool(
            "create_return",
            self.tools.create_return,
            context.order_id,
            reason,
            return_type,
            context.message,
            context.customer_id,
        )
        if created is None:
            return AgentResult(
                "after-sales-agent",
                "failed",
                f"我没有查到订单 {context.order_id}，暂时不能发起售后申请。请您核对订单号。",
                "create_return failed because order was not found.",
            )

        return AgentResult(
            "after-sales-agent",
            "success",
            (
                f"已为订单 {context.order_id} 发起{self._return_type_label(return_type)}申请，"
                f"服务单号是 {created['return_number']}，当前状态为待审核。"
            ),
            f"Created return request id={created['id']} type={return_type}.",
        )

    def _handle_consultation(self, context: CustomerContext) -> AgentResult:
        query = context.message
        entries = self._call_tool("search_faq", self.tools.search_faq, query)
        if not entries:
            return AgentResult(
                "consultation-agent",
                "partial",
                "这个问题我还需要再确认一下。您可以补充具体产品、政策或场景，我再帮您查更准确的说明。",
                f"No FAQ entry matched query={query!r}.",
            )

        entry = entries[0]
        return AgentResult(
            "consultation-agent",
            "success",
            f"根据知识库：{entry['answer']}",
            f"FAQ matched id={entry.get('id')} category={entry.get('category')}.",
        )

    def _handle_complaint(self, context: CustomerContext) -> AgentResult:
        return AgentResult(
            "complaint-agent",
            "needs-escalation",
            "我理解您现在很不满意，这个情况我会按投诉流程优先记录，并为您转交更合适的同事跟进。",
            "Complaint-agent is L2 conversation-only; record/escalation handled by work-order/handoff flow.",
        )

    def _handle_work_order(self, context: CustomerContext) -> AgentResult:
        title = "客户投诉工单" if self._contains_any(context.message, L2_KEYWORDS) else "客户服务工单"
        priority = "P1" if self._contains_any(context.message, ("律师", "监管", "315", "起诉", "法院")) else "P2"
        ticket = self._call_tool(
            "create_ticket",
            self.tools.create_ticket,
            title,
            context.message,
            "incident",
            priority,
            context.customer_id,
            context.order_id,
        )
        return AgentResult(
            "work-order-agent",
            "success",
            f"我已经为您创建工单 {ticket['ticket_number']}，优先级为 {ticket['priority']}，会安排专人继续跟进。",
            f"Created ticket id={ticket['id']} priority={priority}.",
        )

    def _handle_human_handoff(self, context: CustomerContext) -> AgentResult:
        return AgentResult(
            "human-handoff-agent",
            "needs-escalation",
            "这个情况需要人工同事立即介入。我会整理好您的诉求、订单信息和风险点，优先转交人工处理。",
            "Critical L3 trigger or explicit handoff path.",
        )

    def _handle_satisfaction(self, context: CustomerContext) -> list[AgentResult]:
        rating = self._extract_rating(context.message)
        if rating is None:
            return [
                AgentResult(
                    "customer-service-orchestrator",
                    "partial",
                    "如果方便的话，请您用1到5星评价这次服务，我会记录下来帮助我们改进。",
                    "Satisfaction intent detected but no rating was parsed.",
                )
            ]

        survey = self._call_tool(
            "submit_satisfaction",
            self.tools.submit_satisfaction,
            rating,
            context.message,
            context.customer_id,
            context.order_id,
        )
        results = [
            AgentResult(
                "customer-service-orchestrator",
                "success",
                f"感谢您的{rating}星评价，我已经记录下来。",
                f"Created satisfaction survey id={survey['id']}.",
            )
        ]

        if rating <= 3:
            ticket = self._call_tool(
                "create_ticket",
                self.tools.create_ticket,
                f"低分回访工单 -- 客户满意度{rating}星",
                context.message,
                "service_request",
                "P2",
                context.customer_id,
                context.order_id,
            )
            results.append(
                AgentResult(
                    "work-order-agent",
                    "success",
                    f"同时我已创建低分回访工单 {ticket['ticket_number']}，会安排主管跟进改进建议。",
                    f"Created low-score follow-up ticket id={ticket['id']}.",
                )
            )
        return results

    def _handler_for(self, intent: str) -> Callable[[CustomerContext], Any] | None:
        return {
            "order_inquiry": self._handle_order_inquiry,
            "after_sales": self._handle_after_sales,
            "consultation": self._handle_consultation,
            "complaint": self._handle_complaint,
            "work_order": self._handle_work_order,
            "human_handoff": self._handle_human_handoff,
            "satisfaction": self._handle_satisfaction,
        }.get(intent)

    def _record_usage_event(
        self,
        result: dict[str, Any],
        context: CustomerContext,
        failure_reason: str = "",
    ) -> None:
        try:
            import analytics_service

            with database.session_scope() as session:
                analytics_service.record_usage_event_from_result(
                    session,
                    result,
                    customer_id=context.customer_id,
                    order_id=context.order_id,
                    message_length=len(context.message),
                    failure_reason=failure_reason,
                )
        except Exception:
            LOGGER.warning("failed to record customer-service usage analytics", exc_info=True)
            # Analytics must never block the customer response path.
            return

    def _call_tool(self, name: str, func: Callable[..., Any], *args: Any) -> Any:
        try:
            result = func(*args)
        except Exception as exc:
            self.tool_calls.append(ToolCall(name, "failed", str(exc)))
            raise

        summary = "no result" if result is None else self._tool_summary(result)
        self.tool_calls.append(ToolCall(name, "success", summary))
        return result

    def _shipment_result(
        self,
        shipment: dict[str, Any],
        note: str,
        order: dict[str, Any] | None = None,
    ) -> AgentResult:
        latest = shipment["events"][-1] if shipment.get("events") else None
        prefix = f"订单 {order['id']} " if order else ""
        latest_text = (
            f"最新轨迹：{latest['event_time']}，{latest['location']}，{latest['description']}。"
            if latest
            else "目前暂无详细轨迹。"
        )
        return AgentResult(
            "order-inquiry-agent",
            "success",
            (
                f"{prefix}物流状态为 {shipment['status']}，承运商 {shipment['carrier']}，"
                f"运单号 {shipment['tracking_number']}。{latest_text}"
            ),
            f"Shipment lookup succeeded for {note}.",
        )

    def _compose_reply(self, results: list[AgentResult]) -> str:
        if not results:
            return "您好，我是小客。这个问题我还需要再确认一下，请您补充订单号或具体诉求。"

        replies = self._unique([result.customer_reply for result in results if result.customer_reply])
        if len(replies) == 1:
            return replies[0]
        return " ".join(replies)

    def _overall_status(self, results: list[AgentResult], needs_human: bool) -> str:
        if needs_human:
            return "needs-human"
        if any(result.status == "failed" for result in results):
            return "partial"
        if any(result.status == "partial" for result in results):
            return "needs-info"
        return "success"

    def _dedupe_intents_by_priority(
        self, intents: list[IntentAnalysis]
    ) -> list[IntentAnalysis]:
        priority = [
            "human_handoff",
            "satisfaction",
            "complaint",
            "work_order",
            "after_sales",
            "order_inquiry",
            "consultation",
        ]
        by_intent: dict[str, IntentAnalysis] = {}
        for intent in intents:
            existing = by_intent.get(intent.intent)
            if existing is None or intent.confidence > existing.confidence:
                by_intent[intent.intent] = intent
        return sorted(by_intent.values(), key=lambda item: priority.index(item.intent))

    def _classify_emotion(self, message: str) -> str:
        if self._contains_any(message, L3_KEYWORDS):
            return "L3"
        if self._contains_any(message, L2_KEYWORDS):
            return "L2"
        if any(word in message for word in ("太差", "生气", "失望", "垃圾", "破服务")):
            return "L1"
        return "L1"

    @staticmethod
    def _contains_any(message: str, keywords: tuple[str, ...]) -> bool:
        lower = message.lower()
        return any(keyword.lower() in lower for keyword in keywords)

    @staticmethod
    def _extract_order_id(message: str) -> str | None:
        match = ORDER_ID_RE.search(message)
        return match.group(0).upper() if match else None

    @staticmethod
    def _extract_tracking_number(message: str) -> str | None:
        match = TRACKING_RE.search(message)
        return match.group(0).upper() if match else None

    @staticmethod
    def _extract_rating(message: str) -> int | None:
        match = RATING_RE.search(message)
        return int(match.group(1)) if match else None

    @staticmethod
    def _short_reason(message: str) -> str:
        reason = re.sub(r"\s+", " ", message).strip()
        return reason[:80] if reason else "客户售后申请"

    @staticmethod
    def _faq_query(message: str) -> str:
        for keyword in ("退货", "换货", "退款", "物流", "会员", "发票", "保修", "产品"):
            if keyword in message:
                return keyword
        return message[:20]

    @staticmethod
    def _return_type_label(return_type: str) -> str:
        return {"return": "退货", "exchange": "换货", "refund": "退款"}.get(return_type, "售后")

    @staticmethod
    def _tool_summary(result: Any) -> str:
        if isinstance(result, dict):
            for key in ("id", "ticket_number", "return_number", "survey_number", "tracking_number"):
                if key in result:
                    return f"{key}={result[key]}"
            return f"dict keys={','.join(result.keys())}"
        if isinstance(result, list):
            return f"{len(result)} item(s)"
        return str(result)[:120]

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    @staticmethod
    def _get_conversation_state(conversation_id: str | None) -> ConversationState:
        if not conversation_id:
            return ConversationState()
        state = _CONVERSATION_STATES.get(conversation_id)
        if state is None:
            return ConversationState()
        _CONVERSATION_STATES.move_to_end(conversation_id)
        return state

    @staticmethod
    def _remember_conversation_state(context: CustomerContext) -> None:
        if not context.conversation_id:
            return
        existing = _CONVERSATION_STATES.get(context.conversation_id, ConversationState())
        state = ConversationState(
            customer_id=context.customer_id if context.customer_id is not None else existing.customer_id,
            order_id=context.order_id or existing.order_id,
            updated_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        )
        _CONVERSATION_STATES[context.conversation_id] = state
        _CONVERSATION_STATES.move_to_end(context.conversation_id)
        while len(_CONVERSATION_STATES) > MAX_CONVERSATION_STATES:
            _CONVERSATION_STATES.popitem(last=False)





