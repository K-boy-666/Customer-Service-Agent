"""Production-oriented REST API for the customer-service platform."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Iterator

import logging

import analytics_service
import config as runtime_config
import database
import seed_data
import service_layer as svc
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api_dependencies import actor_dependency, request_id_dependency
from orchestrator_api import respond_to_customer_message
from security import (
    Actor,
    customer_destination,
    load_verification,
    request_otp,
    require_idempotency_key,
    require_permission,
    run_idempotent,
    verify_otp,
)


LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        runtime_config.validate_runtime_config()
    except RuntimeError:
        if runtime_config.is_production():
            raise
        LOGGER.warning("runtime configuration is not production-ready", exc_info=True)
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


class TicketCreateRequest(BaseModel):
    title: str
    type: str = "incident"
    priority: str = "P3"
    description: str = ""
    customer_id: int | None = None
    order_id: str | None = None


class ReturnCreateRequest(BaseModel):
    order_id: str
    type: str = "return"
    reason: str
    description: str = ""
    customer_id: int | None = None


class SurveyCreateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    feedback: str = ""
    customer_id: int | None = None
    order_id: str | None = None


def _model_dump(payload: BaseModel) -> dict[str, Any]:
    return payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()


def _verification(session: Session, token: str | None):
    return load_verification(session, token)


@app.get("/api/health")
def health():
    return {"status": "ok", "database_url": database.get_database_url().split("@")[-1]}


@app.get("/api/ready")
def ready(session: Session = Depends(db_session)):
    checks: dict[str, dict[str, Any]] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = {"status": "failed", "detail": str(exc)}

    required_tables = {
        "orders",
        "tickets",
        "returns",
        "satisfaction_surveys",
        "customer_service_usage_events",
        "conversation_states",
    }
    try:
        from sqlalchemy import inspect

        existing = set(inspect(database.get_engine()).get_table_names())
        missing = sorted(required_tables - existing)
        checks["migrations"] = {"status": "ok" if not missing else "failed", "missing_tables": missing}
    except Exception as exc:
        checks["migrations"] = {"status": "failed", "detail": str(exc)}

    cfg = runtime_config.load_runtime_config()
    checks["configuration"] = {
        "status": "ok",
        "app_env": cfg.app_env,
        "otp_provider": cfg.otp_provider,
        "report_timezone": cfg.report_timezone,
        "faq_rag_backend": cfg.faq_rag_backend,
        "oidc_jwks_configured": bool(cfg.oidc_jwks_url),
    }
    checks["rag"] = {"status": "ok", "backend": cfg.faq_rag_backend}
    overall = "ok" if all(check.get("status") == "ok" for check in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}


@app.get("/api/metrics", response_class=PlainTextResponse)
def metrics(session: Session = Depends(db_session)):
    from models import CustomerServiceUsageEvent, ReturnRequest, SatisfactionSurvey, Ticket

    conversations = session.query(CustomerServiceUsageEvent).count()
    handoffs = session.query(CustomerServiceUsageEvent).filter_by(needs_human=1).count()
    tickets = session.query(Ticket).count()
    returns = session.query(ReturnRequest).count()
    surveys = session.query(SatisfactionSurvey).count()
    lines = [
        "# HELP customer_service_conversations_total Total orchestrator conversations recorded.",
        "# TYPE customer_service_conversations_total counter",
        f"customer_service_conversations_total {conversations}",
        "# HELP customer_service_handoffs_total Total conversations that needed human handoff.",
        "# TYPE customer_service_handoffs_total counter",
        f"customer_service_handoffs_total {handoffs}",
        "# HELP customer_service_tickets_total Total tickets.",
        "# TYPE customer_service_tickets_total gauge",
        f"customer_service_tickets_total {tickets}",
        "# HELP customer_service_returns_total Total return requests.",
        "# TYPE customer_service_returns_total gauge",
        f"customer_service_returns_total {returns}",
        "# HELP customer_service_surveys_total Total satisfaction surveys.",
        "# TYPE customer_service_surveys_total gauge",
        f"customer_service_surveys_total {surveys}",
    ]
    return "\n".join(lines) + "\n"


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


@app.post("/api/v2/tickets", status_code=201)
def create_ticket_v2(
    payload: TicketCreateRequest,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    data = _model_dump(payload)
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/v2/tickets",
        key,
        data,
        lambda: (
            svc.create_ticket(
                session,
                actor,
                payload.title,
                payload.description,
                payload.type,
                payload.priority,
                payload.customer_id,
                payload.order_id,
                verification,
                key,
                request_id,
            ),
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


@app.post("/api/v2/returns", status_code=201)
def create_return_v2(
    payload: ReturnCreateRequest,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    data = _model_dump(payload)
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/v2/returns",
        key,
        data,
        lambda: (
            svc.create_return(
                session,
                actor,
                payload.order_id,
                payload.type,
                payload.reason,
                payload.description,
                payload.customer_id,
                verification,
                key,
                request_id,
            ),
            201,
        ),
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


@app.post("/api/v2/surveys", status_code=201)
def submit_survey_v2(
    payload: SurveyCreateRequest,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
    request_id: str = Depends(request_id_dependency),
):
    key = require_idempotency_key(idempotency_key)
    verification = _verification(session, verification_token)
    data = _model_dump(payload)
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/v2/surveys",
        key,
        data,
        lambda: (
            svc.submit_survey(
                session,
                actor,
                payload.rating,
                payload.feedback,
                payload.customer_id,
                payload.order_id,
                verification,
                key,
                request_id,
            ),
            201,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.get("/api/surveys")
def list_surveys(customer_id: int | None = Query(None), rating: int | None = Query(None), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.list_surveys(session, actor, customer_id, rating, limit, offset)

