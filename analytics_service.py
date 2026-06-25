"""Usage analytics collection and daily report aggregation."""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import AuditEvent, CustomerServiceUsageEvent, ReturnRequest, SatisfactionSurvey, Ticket
from security import Actor, require_permission


def report_timezone_name() -> str:
    return os.getenv("REPORT_TIMEZONE", "Asia/Shanghai")


def _report_timezone():
    name = report_timezone_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name)
        raise


def parse_report_date(value: str | date | None, tz: Any | None = None) -> date:
    report_tz = tz or _report_timezone()
    if isinstance(value, date):
        return value
    if not value or value == "yesterday":
        return datetime.now(report_tz).date() - timedelta(days=1)
    if value == "today":
        return datetime.now(report_tz).date()
    return date.fromisoformat(value)


def _window(report_date: date, tz: Any | None = None) -> tuple[datetime, datetime]:
    report_tz = tz or _report_timezone()
    local_start = datetime.combine(report_date, time.min, tzinfo=report_tz)
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = local_end.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _safe_int(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    return int(value)


def _safe_order_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _counter_dict(values: list[Any]) -> dict[str, int]:
    return dict(Counter(str(value) for value in values if value not in (None, "")))


def record_usage_event(
    session: Session,
    *,
    conversation_id: str,
    customer_id: int | None,
    order_id: str | None,
    message_length: int,
    status: str,
    emotional_level: str,
    intents: list[dict[str, Any]],
    dispatched_agents: list[str],
    tool_calls: list[dict[str, Any]],
    needs_human: bool,
    failure_reason: str = "",
) -> CustomerServiceUsageEvent:
    event = CustomerServiceUsageEvent(
        conversation_id=conversation_id,
        customer_id=_safe_int(customer_id),
        order_id=_safe_order_id(order_id),
        message_length=max(0, int(message_length)),
        status=status,
        emotional_level=emotional_level,
        intents=[_sanitize_intent(intent) for intent in intents],
        dispatched_agents=[str(agent) for agent in dispatched_agents],
        tool_calls=[_sanitize_tool_call(call) for call in tool_calls],
        needs_human=1 if needs_human else 0,
        failure_reason=failure_reason[:500],
    )
    session.add(event)
    session.flush()
    return event


def record_usage_event_from_result(
    session: Session,
    result: dict[str, Any],
    *,
    customer_id: int | None,
    order_id: str | None,
    message_length: int,
    failure_reason: str = "",
) -> CustomerServiceUsageEvent:
    return record_usage_event(
        session,
        conversation_id=str(result.get("conversation_id") or ""),
        customer_id=customer_id,
        order_id=order_id,
        message_length=message_length,
        status=str(result.get("status") or "failed"),
        emotional_level=str(result.get("emotional_level") or ""),
        intents=list(result.get("intent_analysis") or []),
        dispatched_agents=list(result.get("dispatched_agents") or []),
        tool_calls=list(result.get("tool_calls") or []),
        needs_human=bool(result.get("needs_human")),
        failure_reason=failure_reason,
    )


def get_usage_analytics(session: Session, actor: Actor, report_date: str | date | None = None) -> dict[str, Any]:
    require_permission(actor, "analytics:read")
    report_tz = _report_timezone()
    target_date = parse_report_date(report_date, report_tz)
    start, end = _window(target_date, report_tz)

    events = (
        session.query(CustomerServiceUsageEvent)
        .filter(CustomerServiceUsageEvent.created_at >= start, CustomerServiceUsageEvent.created_at < end)
        .order_by(CustomerServiceUsageEvent.created_at.asc())
        .all()
    )
    tickets = session.query(Ticket).filter(Ticket.created_at >= start, Ticket.created_at < end).all()
    returns = session.query(ReturnRequest).filter(ReturnRequest.created_at >= start, ReturnRequest.created_at < end).all()
    surveys = session.query(SatisfactionSurvey).filter(SatisfactionSurvey.created_at >= start, SatisfactionSurvey.created_at < end).all()
    audit_events = session.query(AuditEvent).filter(AuditEvent.created_at >= start, AuditEvent.created_at < end).all()
    low_score_followups = (
        session.query(Ticket)
        .filter(
            Ticket.created_at >= start,
            Ticket.created_at < end,
            or_(Ticket.title.like("%低分%"), Ticket.description.like("%低分%")),
        )
        .count()
    )

    intent_names: list[str] = []
    agents: list[str] = []
    tool_names: list[str] = []
    tool_statuses: list[str] = []
    failed_tools: list[dict[str, str]] = []
    for event in events:
        for intent in event.intents or []:
            intent_names.append(str(intent.get("intent") or intent.get("name") or "unknown"))
        agents.extend(event.dispatched_agents or [])
        for call in event.tool_calls or []:
            name = str(call.get("tool") or call.get("name") or "unknown")
            status = str(call.get("status") or "unknown")
            tool_names.append(name)
            tool_statuses.append(status)
            if status != "success":
                failed_tools.append({"tool": name, "status": status, "summary": str(call.get("summary") or "")[:160]})

    ratings = [survey.rating for survey in surveys]
    audit_failures = [event for event in audit_events if event.result != "success"]
    usage = {
        "total_conversations": len(events),
        "unique_customers": len({event.customer_id for event in events if event.customer_id is not None}),
        "status_counts": _counter_dict([event.status for event in events]),
        "emotional_level_counts": _counter_dict([event.emotional_level for event in events]),
        "needs_human_count": sum(1 for event in events if event.needs_human),
        "average_message_length": round(sum(event.message_length for event in events) / len(events), 1) if events else 0,
    }
    routing = {
        "intent_counts": _counter_dict(intent_names),
        "sub_agent_counts": _counter_dict(agents),
        "tool_call_counts": _counter_dict(tool_names),
        "tool_status_counts": _counter_dict(tool_statuses),
        "failed_tools": failed_tools[:10],
    }
    operations = {
        "tickets_created": len(tickets),
        "tickets_by_status": _counter_dict([ticket.status for ticket in tickets]),
        "tickets_by_priority": _counter_dict([ticket.priority for ticket in tickets]),
        "returns_created": len(returns),
        "returns_by_status": _counter_dict([ret.status for ret in returns]),
        "surveys_submitted": len(surveys),
        "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "low_rating_count": sum(1 for rating in ratings if rating <= 3),
        "low_score_followup_tickets": low_score_followups,
    }
    quality = {
        "escalation_rate": round(usage["needs_human_count"] / len(events), 4) if events else 0,
        "failed_tool_call_count": len(failed_tools),
        "audit_failure_count": len(audit_failures),
        "audit_failures_by_permission": _counter_dict([event.permission for event in audit_failures]),
        "orchestrator_failures": [event.failure_reason for event in events if event.failure_reason][:10],
    }
    return {
        "date": target_date.isoformat(),
        "timezone": report_timezone_name(),
        "generated_at": _iso(datetime.now(timezone.utc).replace(tzinfo=None)),
        "window": {"start": _iso(start), "end": _iso(end), "start_utc": _iso(start), "end_utc": _iso(end)},
        "usage": usage,
        "routing": routing,
        "operations": operations,
        "quality_signals": quality,
        "recommendations": build_recommendations(usage, routing, operations, quality),
    }


def build_recommendations(
    usage: dict[str, Any],
    routing: dict[str, Any],
    operations: dict[str, Any],
    quality: dict[str, Any],
) -> list[str]:
    recommendations: list[str] = []
    if usage["total_conversations"] == 0:
        return ["No customer-service usage was recorded for this date; verify traffic capture and scheduled runtime health."]
    if usage["needs_human_count"]:
        recommendations.append("Review escalated conversations and confirm handoff reasons are covered by current routing guidance.")
    if quality["failed_tool_call_count"]:
        recommendations.append("Investigate failed tool calls before the next business day to avoid repeated customer-facing partial resolutions.")
    if operations["low_rating_count"]:
        recommendations.append("Review low satisfaction ratings and ensure follow-up tickets are assigned and progressing.")
    top_agents = routing.get("sub_agent_counts") or {}
    if top_agents:
        busiest = max(top_agents, key=top_agents.get)
        recommendations.append(f"Inspect {busiest} prompts and tool paths; it handled the highest share of routed work.")
    if not recommendations:
        recommendations.append("No major quality issues detected; continue monitoring routing mix and satisfaction trends.")
    return recommendations


def render_markdown_report(analytics: dict[str, Any]) -> str:
    lines = [
        f"# Customer Service Daily Analytics - {analytics['date']}",
        "",
        f"Generated at: {analytics['generated_at']} UTC",
        f"Timezone: {analytics.get('timezone', 'UTC')}",
        f"Window UTC: {analytics.get('window', {}).get('start_utc', analytics.get('window', {}).get('start', ''))} to {analytics.get('window', {}).get('end_utc', analytics.get('window', {}).get('end', ''))}",
        "",
        "## Daily Overview",
        f"- Conversations: {analytics['usage']['total_conversations']}",
        f"- Unique customers: {analytics['usage']['unique_customers']}",
        f"- Status counts: {_format_counts(analytics['usage']['status_counts'])}",
        f"- Emotional levels: {_format_counts(analytics['usage']['emotional_level_counts'])}",
        f"- Human handoffs: {analytics['usage']['needs_human_count']}",
        f"- Average message length: {analytics['usage']['average_message_length']}",
        "",
        "## Routing",
        f"- Intent distribution: {_format_counts(analytics['routing']['intent_counts'])}",
        f"- Sub-agent usage: {_format_counts(analytics['routing']['sub_agent_counts'])}",
        f"- Tool calls: {_format_counts(analytics['routing']['tool_call_counts'])}",
        f"- Tool statuses: {_format_counts(analytics['routing']['tool_status_counts'])}",
        "",
        "## Operations",
        f"- Tickets created: {analytics['operations']['tickets_created']}",
        f"- Tickets by status: {_format_counts(analytics['operations']['tickets_by_status'])}",
        f"- Returns created: {analytics['operations']['returns_created']}",
        f"- Returns by status: {_format_counts(analytics['operations']['returns_by_status'])}",
        f"- Surveys submitted: {analytics['operations']['surveys_submitted']}",
        f"- Average rating: {analytics['operations']['average_rating'] if analytics['operations']['average_rating'] is not None else 'n/a'}",
        f"- Low ratings: {analytics['operations']['low_rating_count']}",
        f"- Low-score follow-up tickets: {analytics['operations']['low_score_followup_tickets']}",
        "",
        "## Quality Signals",
        f"- Escalation rate: {analytics['quality_signals']['escalation_rate']}",
        f"- Failed tool calls: {analytics['quality_signals']['failed_tool_call_count']}",
        f"- Audit failures: {analytics['quality_signals']['audit_failure_count']}",
        f"- Audit failures by permission: {_format_counts(analytics['quality_signals']['audit_failures_by_permission'])}",
        "",
        "## Recommendations",
    ]
    lines.extend(f"- {item}" for item in analytics["recommendations"])
    if analytics["routing"]["failed_tools"]:
        lines.extend(["", "## Failed Tool Samples"])
        lines.extend(f"- {item['tool']} ({item['status']}): {item['summary']}" for item in analytics["routing"]["failed_tools"])
    return "\n".join(lines) + "\n"


def write_markdown_report(analytics: dict[str, Any], output_dir: str | Path = "reports/daily") -> Path:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{analytics['date']}.md"
    path.write_text(render_markdown_report(analytics), encoding="utf-8")
    return path


def _sanitize_intent(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": str(intent.get("intent") or ""),
        "confidence": intent.get("confidence"),
        "suggested_agent": str(intent.get("suggested_agent") or ""),
        "reason": str(intent.get("reason") or "")[:240],
    }


def _sanitize_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": str(call.get("tool") or call.get("name") or ""),
        "status": str(call.get("status") or ""),
        "summary": str(call.get("summary") or "")[:240],
    }


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
