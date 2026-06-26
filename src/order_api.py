"""Production-oriented REST API for the customer-service platform."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Iterator

import analytics_service
import database
import seed_data
import service_layer as svc
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from orchestrator_api import respond_to_customer_message
from security import (
    Actor,
    actor_dependency,
    customer_destination,
    load_verification,
    request_id_dependency,
    request_otp,
    require_idempotency_key,
    require_permission,
    run_idempotent,
    verify_otp,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    if database.is_db_empty():
        session = database.get_session()
        try:
            seed_data.seed(session)
        finally:
            session.close()
    yield


app = FastAPI(title="Order API", version="1.0.0", lifespan=lifespan)


def db_session() -> Iterator[Session]:
    session = database.get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class OrchestratorRequest(BaseModel):
    message: str = Field(..., min_length=1)
    customer_id: int | None = None
    order_id: str | None = None
    conversation_id: str | None = None


class OtpRequest(BaseModel):
    purpose: str = "customer_identity"
    channel: str = "email"
    destination: str | None = None
    customer_id: int | None = None
    order_id: str | None = None


class OtpVerifyRequest(BaseModel):
    challenge_id: str
    code: str


def _model_dump(payload: BaseModel) -> dict[str, Any]:
    return payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()


def _verification(session: Session, token: str | None):
    return load_verification(session, token)


@app.get("/api/health")
def health():
    return {"status": "ok", "database_url": database.get_database_url().split("@")[-1]}


@app.post("/api/auth/otp/request")
def otp_request(payload: OtpRequest, session: Session = Depends(db_session)):
    destination = customer_destination(session, payload.customer_id, payload.channel, payload.destination)
    return request_otp(
        session,
        purpose=payload.purpose,
        channel=payload.channel,
        destination=destination,
        customer_id=payload.customer_id,
        order_id=payload.order_id,
    )


@app.post("/api/auth/otp/verify")
def otp_verify(payload: OtpVerifyRequest, session: Session = Depends(db_session)):
    return verify_otp(session, payload.challenge_id, payload.code)


@app.post("/api/orchestrator/respond")
def orchestrator_respond(
    payload: OrchestratorRequest,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    require_permission(actor, "orchestrator:invoke")
    verification = load_verification(session, verification_token) if verification_token else None
    result = respond_to_customer_message(
        _model_dump(payload),
        actor=actor,
        verification=verification,
        idempotency_key=idempotency_key or "",
        request_id=request_id,
    )
    return result



@app.get("/api/analytics/usage")
def get_usage_analytics(
    date: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return analytics_service.get_usage_analytics(session, actor, date)

@app.get("/api/orders/search")
def search_orders(q: str = Query(...), limit: int = Query(20, ge=1, le=100), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.search_orders(session, actor, q, limit)


@app.get("/api/orders/stats")
def get_order_stats(actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.get_order_stats(session, actor)


@app.get("/api/orders/by-customer")
def get_orders_by_customer(customer: str = Query(...), limit: int = Query(20, ge=1, le=100), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.get_orders_by_customer(session, actor, customer, limit)


@app.get("/api/orders")
def list_orders(
    order_status: str = Query("all", alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.list_orders(session, actor, order_status, limit, offset, start_date, end_date)


@app.get("/api/orders/{order_id}")
def get_order(order_id: str, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session), verification_token: str | None = Header(None, alias="X-Identity-Verification")):
    return svc.get_order(session, actor, order_id, _verification(session, verification_token))


@app.get("/api/orders/{order_id}/shipment")
def get_shipment(order_id: str, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session), verification_token: str | None = Header(None, alias="X-Identity-Verification")):
    return svc.get_shipment(session, actor, order_id, _verification(session, verification_token))


@app.get("/api/shipments/{tracking_number}")
def track_by_number(tracking_number: str, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.track_by_number(session, actor, tracking_number)


@app.get("/api/customers/search")
def search_customers(q: str = Query(...), limit: int = Query(20, ge=1, le=100), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.search_customers(session, actor, q, limit)


@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: int, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session), verification_token: str | None = Header(None, alias="X-Identity-Verification")):
    return svc.get_customer(session, actor, customer_id, _verification(session, verification_token))


@app.post("/api/tickets", status_code=201)
def create_ticket(
    title: str = Query(...),
    type: str = Query("incident"),
    priority: str = Query("P3"),
    description: str = Query(""),
    customer_id: int | None = Query(None),
    order_id: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    payload = {"title": title, "type": type, "priority": priority, "description": description, "customer_id": customer_id, "order_id": order_id}
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/tickets",
        key,
        payload,
        lambda: (
            svc.create_ticket(session, actor, title, description, type, priority, customer_id, order_id, verification, key, request_id),
            201,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.get("/api/tickets")
def list_tickets(status_filter: str | None = Query(None, alias="status"), assignee: str | None = Query(None), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.list_tickets(session, actor, status_filter, assignee, limit, offset)


@app.get("/api/tickets/search")
def search_tickets(query: str = Query(..., alias="q"), limit: int = Query(20, ge=1, le=100), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    data = svc.list_tickets(session, actor, None, None, limit=100, offset=0)["data"]
    matches = [ticket for ticket in data if query in ticket["title"] or query in ticket["description"] or query in ticket["ticket_number"]]
    return {"data": matches[:limit], "total": len(matches[:limit])}


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.get_ticket(session, actor, ticket_id)


@app.patch("/api/tickets/{ticket_id}")
def update_ticket(
    ticket_id: int,
    status_value: str | None = Query(None, alias="status"),
    assignee: str | None = Query(None),
    priority: str | None = Query(None),
    note: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    payload = {"status": status_value, "assignee": assignee, "priority": priority, "note": note}
    response, code, _replayed = run_idempotent(
        session,
        actor,
        f"PATCH /api/tickets/{ticket_id}",
        key,
        payload,
        lambda: (svc.update_ticket(session, actor, ticket_id, status_value, assignee, priority, note, verification, request_id), 200),
    )
    return JSONResponse(status_code=code, content=response)


@app.post("/api/tickets/{ticket_id}/notes")
def add_ticket_note(
    ticket_id: int,
    content: str = Query(...),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    response, code, _replayed = run_idempotent(
        session,
        actor,
        f"POST /api/tickets/{ticket_id}/notes",
        key,
        {"content": content},
        lambda: (svc.update_ticket(session, actor, ticket_id, None, None, None, content, verification, request_id), 200),
    )
    return JSONResponse(status_code=code, content=response)




@app.post("/api/returns", status_code=201)
def create_return(
    order_id: str = Query(...),
    type: str = Query("return"),
    reason: str = Query(...),
    description: str = Query(""),
    customer_id: int | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    payload = {"order_id": order_id, "type": type, "reason": reason, "description": description, "customer_id": customer_id}
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/returns",
        key,
        payload,
        lambda: (svc.create_return(session, actor, order_id, type, reason, description, customer_id, verification, key, request_id), 201),
    )
    return JSONResponse(status_code=code, content=response)


@app.get("/api/returns")
def list_returns(status_filter: str | None = Query(None, alias="status"), customer_id: int | None = Query(None), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.list_returns(session, actor, status_filter, customer_id, limit, offset)


@app.get("/api/returns/{return_id}")
def get_return(return_id: int, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.get_return(session, actor, return_id)


@app.patch("/api/returns/{return_id}")
def update_return_status(
    return_id: int,
    status_value: str = Query(..., alias="status"),
    note: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    response, code, _replayed = run_idempotent(
        session,
        actor,
        f"PATCH /api/returns/{return_id}",
        key,
        {"status": status_value, "note": note},
        lambda: (svc.update_return_status(session, actor, return_id, status_value, note, verification, request_id), 200),
    )
    return JSONResponse(status_code=code, content=response)


@app.get("/api/orders/{order_id}/returns")
def get_order_returns(order_id: str, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    require_permission(actor, "return:read")
    rows = svc.list_returns(session, actor, None, None, 100, 0)["data"]
    data = [row for row in rows if row["order_id"] == order_id]
    return {"data": data, "total": len(data), "order_id": order_id}


@app.post("/api/surveys", status_code=201)
def submit_survey(
    rating: int = Query(..., ge=1, le=5),
    feedback: str = Query(""),
    customer_id: int | None = Query(None),
    order_id: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    payload = {"rating": rating, "feedback": feedback, "customer_id": customer_id, "order_id": order_id}
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/surveys",
        key,
        payload,
        lambda: (svc.submit_survey(session, actor, rating, feedback, customer_id, order_id, verification, key, request_id), 201),
    )
    return JSONResponse(status_code=code, content=response)


@app.get("/api/surveys")
def list_surveys(customer_id: int | None = Query(None), rating: int | None = Query(None), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.list_surveys(session, actor, customer_id, rating, limit, offset)

