"""Authentication, RBAC, OTP, PII masking, audit, and idempotency helpers."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import jwt
from sqlalchemy.orm import Session
from starlette import status
from starlette.exceptions import HTTPException

import config
from models import AuditEvent, Customer, IdempotencyKey, OtpChallenge


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "orchestrator": {"orchestrator:invoke"},
    "order_inquiry": {"order:read", "shipment:read", "customer:read"},
    "consultation": {"faq:read"},
    "after_sales": {"order:read", "shipment:read", "return:create", "return:read", "return:update"},
    "work_order": {"ticket:create", "ticket:read", "ticket:update", "survey:create", "survey:read"},
    "complaint": {"audit:create"},
    "human_handoff": {"audit:create"},
    "data_analysis": {"analytics:read"},
}

READ_PERMISSIONS = {"order:read", "shipment:read", "customer:read", "faq:read", "ticket:read", "return:read", "survey:read", "analytics:read"}


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str
    claims: dict[str, Any]


@dataclass(frozen=True)
class Verification:
    token: str
    challenge_id: str
    customer_id: int | None
    order_id: str | None
    purpose: str


def has_permission(role: str, permission: str) -> bool:
    permissions = ROLE_PERMISSIONS.get(role, set())
    return "*" in permissions or permission in permissions


def require_permission(actor: Actor, permission: str) -> None:
    if not has_permission(actor.role, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "permission_denied",
                "role": actor.role,
                "permission": permission,
            },
        )


def decode_jwt_token(token: str) -> Actor:
    issuer = os.getenv("OIDC_ISSUER", "customer-service-dev")
    audience = os.getenv("OIDC_AUDIENCE", "customer-service-api")
    jwks_url = os.getenv("OIDC_JWKS_URL", "")
    algorithms = [alg.strip() for alg in os.getenv("OIDC_ALGORITHMS", "RS256,HS256").split(",") if alg.strip()]

    if config.is_production() and not jwks_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="oidc_jwks_required")

    try:
        if jwks_url:
            key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key=key, algorithms=algorithms, audience=audience, issuer=issuer)
        else:
            secret = os.getenv("AUTH_DEV_SECRET", "customer-service-dev-secret-min-32-bytes")
            claims = jwt.decode(token, key=secret, algorithms=["HS256"], audience=audience, issuer=issuer)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid_token: {exc}") from exc

    role = claims.get("role")
    subject = claims.get("sub")
    if not role or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_missing_sub_or_role")
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"unknown_role:{role}")
    return Actor(subject=str(subject), role=str(role), claims=claims)


def create_dev_jwt(subject: str, role: str, expires_minutes: int = 60) -> str:
    if config.is_production():
        raise RuntimeError("create_dev_jwt is disabled in production")
    now = datetime.now(timezone.utc)
    payload = {
        "iss": os.getenv("OIDC_ISSUER", "customer-service-dev"),
        "aud": os.getenv("OIDC_AUDIENCE", "customer-service-api"),
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, os.getenv("AUTH_DEV_SECRET", "customer-service-dev-secret-min-32-bytes"), algorithm="HS256")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_actor_from_authorization(authorization: str | None) -> Actor:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    return decode_jwt_token(authorization.split(" ", 1)[1].strip())


def audit_event(
    session: Session,
    actor: Actor,
    permission: str,
    endpoint: str,
    resource_type: str,
    resource_id: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str = "",
    idempotency_key: str = "",
    verification_id: str = "",
    result: str = "success",
    failure_reason: str = "",
) -> None:
    session.add(
        AuditEvent(
            actor_subject=actor.subject,
            actor_role=actor.role,
            permission=permission,
            endpoint=endpoint,
            resource_type=resource_type,
            resource_id=resource_id,
            before_summary=before,
            after_summary=after,
            request_id=request_id,
            idempotency_key=idempotency_key,
            verification_id=verification_id,
            result=result,
            failure_reason=failure_reason,
        )
    )


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[:2]}***@{domain}"


def mask_phone(phone: str | None) -> str | None:
    if not phone or len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def mask_name(name: str | None) -> str | None:
    if not name:
        return name
    if len(name) == 1:
        return "*"
    return f"{name[0]}*"


def mask_address(address: str | None) -> str | None:
    if not address:
        return address
    return f"{address[:6]}***"


def require_idempotency_key(key: str | None) -> str:
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing_idempotency_key")
    return key


def request_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_idempotent(
    session: Session,
    actor: Actor,
    endpoint: str,
    key: str,
    payload: Any,
    operation: Callable[[], tuple[dict[str, Any], int]],
) -> tuple[dict[str, Any], int, bool]:
    digest = request_hash(payload)
    existing = (
        session.query(IdempotencyKey)
        .filter_by(actor_subject=actor.subject, endpoint=endpoint, key=key)
        .one_or_none()
    )
    if existing:
        if existing.request_hash != digest:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="idempotency_key_payload_conflict")
        return existing.response_json, existing.status_code, True

    response, status_code = operation()
    session.add(
        IdempotencyKey(
            key=key,
            actor_subject=actor.subject,
            endpoint=endpoint,
            request_hash=digest,
            response_json=response,
            status_code=status_code,
        )
    )
    session.flush()
    return response, status_code, False


def request_otp(
    session: Session,
    purpose: str,
    channel: str,
    destination: str,
    customer_id: int | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    if config.is_production() and os.getenv("OTP_PROVIDER", "dev").lower() in {"", "dev", "debug", "local"}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="otp_provider_not_configured")

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge_id = secrets.token_urlsafe(18)
    challenge = OtpChallenge(
        challenge_id=challenge_id,
        purpose=purpose,
        customer_id=customer_id,
        order_id=order_id,
        channel=channel,
        destination=destination,
        code_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        expires_at=utcnow() + timedelta(minutes=int(os.getenv("OTP_TTL_MINUTES", "10"))),
    )
    session.add(challenge)
    session.flush()
    actor = Actor("otp-provider", "admin", {})
    audit_event(
        session,
        actor,
        "otp:request",
        "/api/auth/otp/request",
        "otp_challenge",
        challenge_id,
        after={"purpose": purpose, "channel": channel, "destination": mask_email(destination) if "@" in destination else mask_phone(destination)},
        result="success",
    )
    response = {"challenge_id": challenge_id, "expires_at": challenge.expires_at.isoformat()}
    if os.getenv("OTP_PROVIDER", "dev") == "dev":
        response["dev_code"] = code
    return response


def verify_otp(session: Session, challenge_id: str, code: str) -> dict[str, Any]:
    challenge = session.query(OtpChallenge).filter_by(challenge_id=challenge_id).one_or_none()
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="otp_challenge_not_found")
    if challenge.verified_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="otp_already_verified")
    if challenge.expires_at < utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="otp_expired")
    if challenge.code_hash != hashlib.sha256(code.encode("utf-8")).hexdigest():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="otp_invalid")

    token = secrets.token_urlsafe(32)
    challenge.verification_token = token
    challenge.verified_at = utcnow()
    session.flush()
    audit_event(
        session,
        Actor("otp-provider", "admin", {}),
        "otp:verify",
        "/api/auth/otp/verify",
        "otp_challenge",
        challenge_id,
        after={"verified": True, "customer_id": challenge.customer_id, "order_id": challenge.order_id},
        result="success",
    )
    return {
        "verification_token": token,
        "challenge_id": challenge.challenge_id,
        "customer_id": challenge.customer_id,
        "order_id": challenge.order_id,
        "purpose": challenge.purpose,
    }


def load_verification(session: Session, token: str | None) -> Verification:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_identity_verification")
    challenge = session.query(OtpChallenge).filter_by(verification_token=token).one_or_none()
    if challenge is None or challenge.verified_at is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_identity_verification")
    if challenge.expires_at < utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="expired_identity_verification")
    return Verification(
        token=token,
        challenge_id=challenge.challenge_id,
        customer_id=challenge.customer_id,
        order_id=challenge.order_id,
        purpose=challenge.purpose,
    )


def assert_verification_matches(
    verification: Verification,
    customer_id: int | None = None,
    order_id: str | None = None,
) -> None:
    protected_scope_requested = customer_id is not None or order_id is not None
    verification_has_scope = verification.customer_id is not None or verification.order_id is not None
    if protected_scope_requested and not verification_has_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verification_scope_required")

    if verification.order_id is not None:
        if order_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verification_order_scope_required")
        if verification.order_id != order_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verification_order_mismatch")
        if verification.customer_id is not None and customer_id is not None and verification.customer_id != customer_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verification_customer_mismatch")
        return

    if customer_id is not None and verification.customer_id != customer_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verification_customer_mismatch")

    if order_id is not None and verification.customer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verification_customer_scope_required")



def customer_destination(session: Session, customer_id: int | None, channel: str, destination: str | None) -> str:
    if destination:
        return destination
    if customer_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="customer_id_or_destination_required")
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="customer_not_found")
    return customer.email if channel == "email" else customer.phone
