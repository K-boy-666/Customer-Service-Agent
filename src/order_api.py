"""Production-oriented REST API for the customer-service platform."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess
from pydantic import BaseModel, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.orm import Session

import analytics_service
import config as runtime_config
import database
import seed_data
import service_layer as svc
from api_dependencies import actor_dependency, request_id_dependency
from log_config import configure_logging
from metrics import (
    CONVERSATIONS_TOTAL,
    HANDOFFS_TOTAL,
    RETURNS_TOTAL,
    SURVEYS_TOTAL,
    TICKETS_TOTAL,
)
from orchestrator_api import respond_to_customer_message
from rate_limit import LIMIT_ORCHESTRATOR, LIMIT_OTP, LIMIT_READ, LIMIT_WRITE, limiter
from request_logging import StructuredRequestLoggingMiddleware
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

_cfg = runtime_config.load_runtime_config()
configure_logging(
    log_level=_cfg.log_level,
    json_logs=_cfg.log_json,
    log_to_stderr=False,
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
    if runtime_config.is_production():
        # Production: schema is managed by alembic (docker-entrypoint.sh runs
        # `alembic upgrade head` before the API starts). Never call create_all
        # or seed dev data in production.
        pass
    else:
        database.init_db()
        if database.is_db_empty():
            session = database.get_session()
            try:
                seed_data.seed(session)
            finally:
                session.close()
    yield


_is_production = runtime_config.is_production()

app = FastAPI(
    title="Customer Service Agent 2.0 — Order API",
    version="1.0.0",
    description=(
        "客服智能体 2.0 生产 REST API。提供订单查询、工单生命周期、"
        "退换货、满意度调查、OTP 身份核验与编排器对话入口。\n\n"
        "权限模型: JWT Actor + 权限位(L0 只读 / L1 可写 / L2 对话)。"
        "写端点需 `Idempotency-Key` 头,受保护资源需 `X-Identity-Verification`。"
    ),
    docs_url=None if _is_production else "/api/docs",
    redoc_url=None if _is_production else "/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=[
        {"name": "health", "description": "健康检查与就绪探针"},
        {"name": "auth", "description": "OTP 身份核验"},
        {"name": "orchestrator", "description": "客户对话编排入口"},
        {"name": "orders", "description": "订单与物流查询(L0 只读)"},
        {"name": "tickets", "description": "工单 CRUD(L1 可写)"},
        {"name": "returns", "description": "退换货生命周期(L1 可写)"},
        {"name": "surveys", "description": "满意度调查"},
        {"name": "analytics", "description": "使用量分析"},
    ],
    lifespan=lifespan,
)


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(StructuredRequestLoggingMiddleware)


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


@app.get("/api/health", tags=["health"])
def health():
    return {"status": "ok", "database_url": database.get_database_url().split("@")[-1]}


@app.get("/api/ready", tags=["health"])
def ready():
    checks: dict[str, dict[str, Any]] = {}
    try:
        session = database.get_session()
        try:
            session.execute(text("SELECT 1"))
            checks["database"] = {"status": "ok"}
        except Exception as exc:
            checks["database"] = {"status": "failed", "detail": str(exc)}
        finally:
            session.close()
    except Exception as exc:
        checks["database"] = {"status": "failed", "detail": str(exc)}

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
    return JSONResponse(
        status_code=200 if overall == "ok" else 503,
        content={"status": overall, "checks": checks},
    )


@app.get("/api/metrics", tags=["health"])
def metrics(session: Session = Depends(db_session)):
    from models import CustomerServiceUsageEvent, ReturnRequest, SatisfactionSurvey, Ticket

    conversations = session.query(CustomerServiceUsageEvent).count()
    handoffs = session.query(CustomerServiceUsageEvent).filter_by(needs_human=1).count()
    tickets = session.query(Ticket).count()
    returns = session.query(ReturnRequest).count()
    surveys = session.query(SatisfactionSurvey).count()

    # Set gauge values from current DB state at scrape time.
    CONVERSATIONS_TOTAL.set(conversations)
    HANDOFFS_TOTAL.set(handoffs)
    TICKETS_TOTAL.set(tickets)
    RETURNS_TOTAL.set(returns)
    SURVEYS_TOTAL.set(surveys)

    # In multiprocess mode, create a fresh per-request registry with
    # MultiProcessCollector to aggregate metrics from all worker processes.
    # In single-process mode, generate_latest() uses the default REGISTRY.
    if "PROMETHEUS_MULTIPROC_DIR" in os.environ or os.getenv("prometheus_multiproc_dir"):
        registry = CollectorRegistry(support_collectors_without_names=True)
        multiprocess.MultiProcessCollector(registry)
        data = generate_latest(registry)
    else:
        data = generate_latest()

    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.post("/api/auth/otp/request", tags=["auth"])
@limiter.limit(LIMIT_OTP)
def otp_request(request: Request, payload: OtpRequest, session: Session = Depends(db_session)):
    destination = customer_destination(session, payload.customer_id, payload.channel, payload.destination)
    return request_otp(
        session,
        purpose=payload.purpose,
        channel=payload.channel,
        destination=destination,
        customer_id=payload.customer_id,
        order_id=payload.order_id,
    )


@app.post("/api/auth/otp/verify", tags=["auth"])
@limiter.limit(LIMIT_OTP)
def otp_verify(request: Request, payload: OtpVerifyRequest, session: Session = Depends(db_session)):
    return verify_otp(session, payload.challenge_id, payload.code)


@app.post("/api/orchestrator/respond")
@limiter.limit(LIMIT_ORCHESTRATOR)
def orchestrator_respond(
    request: Request,
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


@app.get("/api/analytics/usage", tags=["analytics"])
@limiter.limit(LIMIT_READ)
def get_usage_analytics(
    request: Request,
    date: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return analytics_service.get_usage_analytics(session, actor, date)


@app.get("/api/orders/search", tags=["orders"])
@limiter.limit(LIMIT_READ)
def search_orders(
    request: Request,
    q: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.search_orders(session, actor, q, limit)


@app.get("/api/orders/stats", tags=["orders"])
@limiter.limit(LIMIT_READ)
def get_order_stats(request: Request, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)):
    return svc.get_order_stats(session, actor)


@app.get("/api/orders/by-customer", tags=["orders"])
@limiter.limit(LIMIT_READ)
def get_orders_by_customer(
    request: Request,
    customer: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.get_orders_by_customer(session, actor, customer, limit)


@app.get("/api/orders", tags=["orders"])
@limiter.limit(LIMIT_READ)
def list_orders(
    request: Request,
    order_status: str = Query("all", alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.list_orders(session, actor, order_status, limit, offset, start_date, end_date)


@app.get("/api/orders/{order_id}", tags=["orders"])
@limiter.limit(LIMIT_READ)
def get_order(
    request: Request,
    order_id: str,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
):
    return svc.get_order(session, actor, order_id, _verification(session, verification_token))


@app.get("/api/orders/{order_id}/shipment", tags=["orders"])
@limiter.limit(LIMIT_READ)
def get_shipment(
    request: Request,
    order_id: str,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
):
    return svc.get_shipment(session, actor, order_id, _verification(session, verification_token))


@app.get("/api/shipments/{tracking_number}", tags=["orders"])
@limiter.limit(LIMIT_READ)
def track_by_number(
    request: Request,
    tracking_number: str,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.track_by_number(session, actor, tracking_number)


@app.get("/api/customers/search", tags=["orders"])
@limiter.limit(LIMIT_READ)
def search_customers(
    request: Request,
    q: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.search_customers(session, actor, q, limit)


@app.get("/api/customers/{customer_id}", tags=["orders"])
@limiter.limit(LIMIT_READ)
def get_customer(
    request: Request,
    customer_id: int,
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
    verification_token: str | None = Header(None, alias="X-Identity-Verification"),
):
    return svc.get_customer(session, actor, customer_id, _verification(session, verification_token))


@app.post("/api/tickets", status_code=201, tags=["tickets"])
@limiter.limit(LIMIT_WRITE)
def create_ticket(
    request: Request,
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
    payload = {
        "title": title,
        "type": type,
        "priority": priority,
        "description": description,
        "customer_id": customer_id,
        "order_id": order_id,
    }
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/tickets",
        key,
        payload,
        lambda: (
            svc.create_ticket(
                session, actor, title, description, type, priority, customer_id, order_id, verification, key, request_id
            ),
            201,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.post("/api/v2/tickets", status_code=201, tags=["tickets"])
@limiter.limit(LIMIT_WRITE)
def create_ticket_v2(
    request: Request,
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


@app.get("/api/tickets", tags=["tickets"])
@limiter.limit(LIMIT_READ)
def list_tickets(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    assignee: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.list_tickets(session, actor, status_filter, assignee, limit, offset)


@app.get("/api/tickets/search", tags=["tickets"])
@limiter.limit(LIMIT_READ)
def search_tickets(
    request: Request,
    query: str = Query(..., alias="q"),
    limit: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    data = svc.list_tickets(session, actor, None, None, limit=100, offset=0)["data"]
    matches = [
        ticket
        for ticket in data
        if query in ticket["title"] or query in ticket["description"] or query in ticket["ticket_number"]
    ]
    return {"data": matches[:limit], "total": len(matches[:limit])}


@app.get("/api/tickets/{ticket_id}", tags=["tickets"])
@limiter.limit(LIMIT_READ)
def get_ticket(
    request: Request, ticket_id: int, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)
):
    return svc.get_ticket(session, actor, ticket_id)


@app.patch("/api/tickets/{ticket_id}", tags=["tickets"])
@limiter.limit(LIMIT_WRITE)
def update_ticket(
    request: Request,
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
        lambda: (
            svc.update_ticket(
                session, actor, ticket_id, status_value, assignee, priority, note, verification, request_id
            ),
            200,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.post("/api/tickets/{ticket_id}/notes", tags=["tickets"])
@limiter.limit(LIMIT_WRITE)
def add_ticket_note(
    request: Request,
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
        lambda: (
            svc.update_ticket(session, actor, ticket_id, None, None, None, content, verification, request_id),
            200,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.post("/api/returns", status_code=201, tags=["returns"])
@limiter.limit(LIMIT_WRITE)
def create_return(
    request: Request,
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
    payload = {
        "order_id": order_id,
        "type": type,
        "reason": reason,
        "description": description,
        "customer_id": customer_id,
    }
    response, code, _replayed = run_idempotent(
        session,
        actor,
        "POST /api/returns",
        key,
        payload,
        lambda: (
            svc.create_return(
                session, actor, order_id, type, reason, description, customer_id, verification, key, request_id
            ),
            201,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.post("/api/v2/returns", status_code=201, tags=["returns"])
@limiter.limit(LIMIT_WRITE)
def create_return_v2(
    request: Request,
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


@app.get("/api/returns", tags=["returns"])
@limiter.limit(LIMIT_READ)
def list_returns(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    customer_id: int | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.list_returns(session, actor, status_filter, customer_id, limit, offset)


@app.get("/api/returns/{return_id}", tags=["returns"])
@limiter.limit(LIMIT_READ)
def get_return(
    request: Request, return_id: int, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)
):
    return svc.get_return(session, actor, return_id)


@app.patch("/api/returns/{return_id}", tags=["returns"])
@limiter.limit(LIMIT_WRITE)
def update_return_status(
    request: Request,
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
        lambda: (
            svc.update_return_status(session, actor, return_id, status_value, note, verification, request_id),
            200,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.get("/api/orders/{order_id}/returns", tags=["returns"])
@limiter.limit(LIMIT_READ)
def get_order_returns(
    request: Request, order_id: str, actor: Actor = Depends(actor_dependency), session: Session = Depends(db_session)
):
    require_permission(actor, "return:read")
    rows = svc.list_returns(session, actor, None, None, 100, 0)["data"]
    data = [row for row in rows if row["order_id"] == order_id]
    return {"data": data, "total": len(data), "order_id": order_id}


@app.post("/api/surveys", status_code=201, tags=["surveys"])
@limiter.limit(LIMIT_WRITE)
def submit_survey(
    request: Request,
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
        lambda: (
            svc.submit_survey(session, actor, rating, feedback, customer_id, order_id, verification, key, request_id),
            201,
        ),
    )
    return JSONResponse(status_code=code, content=response)


@app.post("/api/v2/surveys", status_code=201, tags=["surveys"])
@limiter.limit(LIMIT_WRITE)
def submit_survey_v2(
    request: Request,
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


@app.get("/api/surveys", tags=["surveys"])
@limiter.limit(LIMIT_READ)
def list_surveys(
    request: Request,
    customer_id: int | None = Query(None),
    rating: int | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(actor_dependency),
    session: Session = Depends(db_session),
):
    return svc.list_surveys(session, actor, customer_id, rating, limit, offset)
