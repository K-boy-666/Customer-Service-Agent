"""Business services with RBAC, PII handling, audit, and state machines."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload
from starlette import status
from starlette.exceptions import HTTPException

import database
from models import (
    Customer,
    Order,
    OrderItem,
    ReturnRequest,
    SatisfactionSurvey,
    Shipment,
    Ticket,
    TicketNote,
)
from security import (
    Actor,
    Verification,
    assert_verification_matches,
    audit_event,
    mask_address,
    mask_email,
    mask_name,
    mask_phone,
    require_permission,
)

TICKET_TRANSITIONS = {
    "new": {"assigned", "closed"},
    "assigned": {"in_progress", "pending", "closed"},
    "in_progress": {"pending", "resolved", "assigned"},
    "pending": {"in_progress", "resolved", "closed"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
}

RETURN_TRANSITIONS = {
    "pending": {"approved", "rejected"},
    "approved": {"in_transit", "refunded"},
    "rejected": set(),
    "in_transit": {"received"},
    "received": {"refunded"},
    "refunded": {"completed"},
    "completed": set(),
}

_NUMBER_LOCKS: dict[str, Any] = {}
_NUMBER_SEQUENCES: dict[str, int] = {}


def _iso(dt: Any) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt is not None else ""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def serialize_order(order: Order, reveal_pii: bool = False) -> dict[str, Any]:
    customer = order.customer
    return {
        "id": order.id,
        "order_number": order.order_number,
        "customer_id": order.customer_id,
        "customer_name": customer.name if reveal_pii else mask_name(customer.name),
        "customer_email": customer.email if reveal_pii else mask_email(customer.email),
        "customer_phone": customer.phone if reveal_pii else mask_phone(customer.phone),
        "status": order.status,
        "total_amount": order.total_amount,
        "currency": order.currency,
        "items": [{"sku": item.sku, "name": item.name, "qty": item.qty, "price": item.price} for item in order.items],
        "shipping_address": order.shipping_address if reveal_pii else mask_address(order.shipping_address),
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def serialize_shipment(shipment: Shipment) -> dict[str, Any]:
    events = sorted(shipment.events, key=lambda event: event.event_time)
    return {
        "order_id": shipment.order_id,
        "carrier": shipment.carrier,
        "tracking_number": shipment.tracking_number,
        "status": shipment.status,
        "estimated_delivery": shipment.estimated_delivery,
        "events": [
            {
                "status": event.status,
                "location": event.location,
                "description": event.description,
                "event_time": event.event_time,
            }
            for event in events
        ],
    }


def serialize_customer(
    customer: Customer, reveal_pii: bool = False, summary: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": customer.id,
        "name": customer.name if reveal_pii else mask_name(customer.name),
        "email": customer.email if reveal_pii else mask_email(customer.email),
        "phone": customer.phone if reveal_pii else mask_phone(customer.phone),
        "membership_tier": customer.membership_tier,
        "points": customer.points,
        "joined_at": customer.joined_at,
        "order_summary": summary or {},
    }


def serialize_ticket(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "title": ticket.title,
        "type": ticket.type,
        "priority": ticket.priority,
        "status": ticket.status,
        "description": ticket.description,
        "customer_id": ticket.customer_id,
        "order_id": ticket.order_id,
        "assignee": ticket.assignee,
        "department": ticket.department,
        "created_at": _iso(ticket.created_at),
        "updated_at": _iso(ticket.updated_at),
        "notes": [
            {"id": note.id, "content": note.content, "author": note.author, "created_at": _iso(note.created_at)}
            for note in ticket.notes
        ],
    }


def serialize_return(ret: ReturnRequest) -> dict[str, Any]:
    return {
        "id": ret.id,
        "return_number": ret.return_number,
        "order_id": ret.order_id,
        "customer_id": ret.customer_id,
        "type": ret.type,
        "reason": ret.reason,
        "description": ret.description,
        "status": ret.status,
        "refund_amount": ret.refund_amount,
        "created_at": _iso(ret.created_at),
        "updated_at": _iso(ret.updated_at),
    }


def serialize_survey(survey: SatisfactionSurvey) -> dict[str, Any]:
    return {
        "id": survey.id,
        "survey_number": survey.survey_number,
        "customer_id": survey.customer_id,
        "order_id": survey.order_id,
        "rating": survey.rating,
        "feedback_text": survey.feedback_text,
        "created_at": _iso(survey.created_at),
    }


def search_orders(session: Session, actor: Actor, query: str, limit: int = 20) -> dict[str, Any]:
    require_permission(actor, "order:read")
    like = f"%{query}%"
    orders = (
        session.query(Order)
        .join(Order.customer)
        .outerjoin(Order.items)
        .options(selectinload(Order.customer), selectinload(Order.items))
        .filter(
            or_(
                Order.id.like(like),
                Order.order_number.like(like),
                Customer.name.like(like),
                Customer.email.like(like),
                OrderItem.name.like(like),
                OrderItem.sku.like(like),
            )
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"data": [serialize_order(order, reveal_pii=False) for order in orders], "total": len(orders)}


def get_order(session: Session, actor: Actor, order_id: str, verification: Verification) -> dict[str, Any]:
    require_permission(actor, "order:read")
    order = (
        session.query(Order)
        .options(selectinload(Order.customer), selectinload(Order.items))
        .filter_by(id=order_id)
        .one_or_none()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Order '{order_id}' not found")
    assert_verification_matches(verification, customer_id=order.customer_id, order_id=order.id)
    return serialize_order(order, reveal_pii=True)


def list_orders(
    session: Session,
    actor: Actor,
    order_status: str = "all",
    limit: int = 20,
    offset: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    require_permission(actor, "order:read")
    query = session.query(Order).options(selectinload(Order.customer), selectinload(Order.items))
    if order_status != "all":
        query = query.filter_by(status=order_status)
    if start_date:
        query = query.filter(Order.created_at >= start_date)
    if end_date:
        query = query.filter(Order.created_at <= end_date + "T23:59:59")
    total = query.count()
    orders = query.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "data": [serialize_order(order, reveal_pii=False) for order in orders],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def get_order_stats(session: Session, actor: Actor) -> dict[str, Any]:
    require_permission(actor, "order:read")
    rows = (
        session.query(Order.status, func.count(Order.id), func.coalesce(func.sum(Order.total_amount), 0))
        .group_by(Order.status)
        .all()
    )
    by_status = {status_: count for status_, count, _revenue in rows}
    revenue = sum(float(revenue or 0) for status_, _count, revenue in rows if status_ != "cancelled")
    return {"total_orders": sum(by_status.values()), "revenue": round(revenue, 2), "by_status": by_status}


def get_orders_by_customer(session: Session, actor: Actor, customer: str, limit: int = 20) -> dict[str, Any]:
    require_permission(actor, "order:read")
    like = f"%{customer}%"
    orders = (
        session.query(Order)
        .join(Order.customer)
        .options(selectinload(Order.customer), selectinload(Order.items))
        .filter(or_(Customer.name.like(like), Customer.email.like(like), Customer.phone.like(like)))
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"data": [serialize_order(order, reveal_pii=False) for order in orders], "total": len(orders)}


def get_shipment(session: Session, actor: Actor, order_id: str, verification: Verification) -> dict[str, Any]:
    require_permission(actor, "shipment:read")
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Order '{order_id}' not found")
    assert_verification_matches(verification, customer_id=order.customer_id, order_id=order_id)
    shipment = session.query(Shipment).options(selectinload(Shipment.events)).filter_by(order_id=order_id).one_or_none()
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No shipment for order '{order_id}'")
    return serialize_shipment(shipment)


def track_by_number(session: Session, actor: Actor, tracking_number: str) -> dict[str, Any]:
    require_permission(actor, "shipment:read")
    shipment = (
        session.query(Shipment)
        .options(selectinload(Shipment.events))
        .filter_by(tracking_number=tracking_number)
        .one_or_none()
    )
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tracking '{tracking_number}' not found")
    return serialize_shipment(shipment)


def search_customers(session: Session, actor: Actor, query: str, limit: int = 20) -> dict[str, Any]:
    require_permission(actor, "customer:read")
    like = f"%{query}%"
    customers = (
        session.query(Customer)
        .filter(or_(Customer.name.like(like), Customer.email.like(like), Customer.phone.like(like)))
        .limit(limit)
        .all()
    )
    return {"data": [serialize_customer(customer, reveal_pii=False) for customer in customers], "total": len(customers)}


def get_customer(session: Session, actor: Actor, customer_id: int, verification: Verification) -> dict[str, Any]:
    require_permission(actor, "customer:read")
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer '{customer_id}' not found")
    assert_verification_matches(verification, customer_id=customer_id)
    total_orders, total_spent = (
        session.query(func.count(Order.id), func.coalesce(func.sum(Order.total_amount), 0))
        .filter_by(customer_id=customer_id)
        .one()
    )
    return serialize_customer(
        customer, reveal_pii=True, summary={"total_orders": total_orders, "total_spent": round(total_spent or 0, 2)}
    )


def create_ticket(
    session: Session,
    actor: Actor,
    title: str,
    description: str,
    ticket_type: str,
    priority: str,
    customer_id: int | None,
    order_id: str | None,
    verification: Verification,
    idempotency_key: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    require_permission(actor, "ticket:create")
    if order_id and customer_id is None:
        order = session.get(Order, order_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Order '{order_id}' not found")
        customer_id = order.customer_id
    assert_verification_matches(verification, customer_id=customer_id, order_id=order_id)
    ticket = Ticket(
        ticket_number=database.get_number_sequencer().next_number(session, Ticket.ticket_number, "TK", "ticket"),
        title=title,
        description=description,
        type=ticket_type,
        priority=priority,
        customer_id=customer_id,
        order_id=order_id,
    )
    session.add(ticket)
    session.flush()
    session.add(TicketNote(ticket_id=ticket.id, content="工单已创建，等待处理。", author=actor.role))
    result = serialize_ticket(ticket)
    audit_event(
        session,
        actor,
        "ticket:create",
        "create_ticket",
        "ticket",
        str(ticket.id),
        after=result,
        request_id=request_id,
        idempotency_key=idempotency_key,
        verification_id=verification.challenge_id,
    )
    return result


def update_ticket(
    session: Session,
    actor: Actor,
    ticket_id: int,
    new_status: str | None = None,
    assignee: str | None = None,
    priority: str | None = None,
    note: str | None = None,
    verification: Verification | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    require_permission(actor, "ticket:update")
    ticket = session.query(Ticket).options(selectinload(Ticket.notes)).filter_by(id=ticket_id).one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket {ticket_id} not found")
    if verification:
        assert_verification_matches(verification, customer_id=ticket.customer_id, order_id=ticket.order_id)
    before = serialize_ticket(ticket)
    if new_status and new_status != ticket.status:
        allowed = TICKET_TRANSITIONS.get(ticket.status, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"illegal_ticket_transition:{ticket.status}->{new_status}"
            )
        ticket.status = new_status
    if assignee is not None:
        ticket.assignee = assignee
    if priority is not None:
        ticket.priority = priority
    if note:
        session.add(TicketNote(ticket_id=ticket.id, content=note, author=actor.role))
    ticket.updated_at = _now()
    session.flush()
    after = serialize_ticket(ticket)
    audit_event(
        session,
        actor,
        "ticket:update",
        "update_ticket",
        "ticket",
        str(ticket.id),
        before=before,
        after=after,
        request_id=request_id,
        verification_id=verification.challenge_id if verification else "",
    )
    return after


def list_tickets(
    session: Session,
    actor: Actor,
    ticket_status: str | None = None,
    assignee: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    require_permission(actor, "ticket:read")
    query = session.query(Ticket).options(selectinload(Ticket.notes))
    if ticket_status:
        query = query.filter_by(status=ticket_status)
    if assignee:
        query = query.filter_by(assignee=assignee)
    total = query.count()
    tickets = query.order_by(Ticket.created_at.desc()).offset(offset).limit(limit).all()
    return {"data": [serialize_ticket(ticket) for ticket in tickets], "total": total, "offset": offset, "limit": limit}


def get_ticket(session: Session, actor: Actor, ticket_id: int) -> dict[str, Any]:
    require_permission(actor, "ticket:read")
    ticket = session.query(Ticket).options(selectinload(Ticket.notes)).filter_by(id=ticket_id).one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket {ticket_id} not found")
    return serialize_ticket(ticket)


def create_return(
    session: Session,
    actor: Actor,
    order_id: str,
    return_type: str,
    reason: str,
    description: str,
    customer_id: int | None,
    verification: Verification,
    idempotency_key: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    require_permission(actor, "return:create")
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Order '{order_id}' not found")
    assert_verification_matches(verification, customer_id=customer_id or order.customer_id, order_id=order_id)
    ret = ReturnRequest(
        return_number=database.get_number_sequencer().next_number(
            session, ReturnRequest.return_number, "RMA", "return"
        ),
        order_id=order_id,
        customer_id=customer_id or order.customer_id,
        type=return_type,
        reason=reason,
        description=description,
        status="pending",
    )
    session.add(ret)
    session.flush()
    result = serialize_return(ret)
    audit_event(
        session,
        actor,
        "return:create",
        "create_return",
        "return",
        str(ret.id),
        after=result,
        request_id=request_id,
        idempotency_key=idempotency_key,
        verification_id=verification.challenge_id,
    )
    return result


def update_return_status(
    session: Session,
    actor: Actor,
    return_id: int,
    new_status: str,
    note: str | None = None,
    verification: Verification | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    require_permission(actor, "return:update")
    ret = session.get(ReturnRequest, return_id)
    if ret is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Return {return_id} not found")
    if verification:
        assert_verification_matches(verification, customer_id=ret.customer_id, order_id=ret.order_id)
    allowed = RETURN_TRANSITIONS.get(ret.status, set())
    if new_status != ret.status and new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"illegal_return_transition:{ret.status}->{new_status}"
        )
    before = serialize_return(ret)
    ret.status = new_status
    ret.updated_at = _now()
    session.flush()
    after = serialize_return(ret)
    audit_event(
        session,
        actor,
        "return:update",
        "update_return_status",
        "return",
        str(ret.id),
        before=before,
        after={**after, "note": note or ""},
        request_id=request_id,
        verification_id=verification.challenge_id if verification else "",
    )
    return after


def list_returns(
    session: Session,
    actor: Actor,
    return_status: str | None = None,
    customer_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    require_permission(actor, "return:read")
    query = session.query(ReturnRequest)
    if return_status:
        query = query.filter_by(status=return_status)
    if customer_id:
        query = query.filter_by(customer_id=customer_id)
    total = query.count()
    rows = query.order_by(ReturnRequest.created_at.desc()).offset(offset).limit(limit).all()
    return {"data": [serialize_return(ret) for ret in rows], "total": total, "offset": offset, "limit": limit}


def get_return(session: Session, actor: Actor, return_id: int) -> dict[str, Any]:
    require_permission(actor, "return:read")
    ret = session.get(ReturnRequest, return_id)
    if ret is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Return {return_id} not found")
    return serialize_return(ret)


def submit_survey(
    session: Session,
    actor: Actor,
    rating: int,
    feedback: str,
    customer_id: int | None,
    order_id: str | None,
    verification: Verification,
    idempotency_key: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    require_permission(actor, "survey:create")
    if order_id and customer_id is None:
        order = session.get(Order, order_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Order '{order_id}' not found")
        customer_id = order.customer_id
    assert_verification_matches(verification, customer_id=customer_id, order_id=order_id)
    survey = SatisfactionSurvey(
        survey_number=database.get_number_sequencer().next_number(
            session, SatisfactionSurvey.survey_number, "SAT", "survey"
        ),
        customer_id=customer_id,
        order_id=order_id,
        rating=rating,
        feedback_text=feedback,
    )
    session.add(survey)
    session.flush()
    result = serialize_survey(survey)
    audit_event(
        session,
        actor,
        "survey:create",
        "submit_survey",
        "survey",
        str(survey.id),
        after=result,
        request_id=request_id,
        idempotency_key=idempotency_key,
        verification_id=verification.challenge_id,
    )
    return result


def list_surveys(
    session: Session,
    actor: Actor,
    customer_id: int | None = None,
    rating: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    require_permission(actor, "survey:read")
    query = session.query(SatisfactionSurvey)
    if customer_id:
        query = query.filter_by(customer_id=customer_id)
    if rating:
        query = query.filter_by(rating=rating)
    total = query.count()
    rows = query.order_by(SatisfactionSurvey.created_at.desc()).offset(offset).limit(limit).all()
    return {"data": [serialize_survey(survey) for survey in rows], "total": total, "offset": offset, "limit": limit}
