"""FastAPI-only dependency helpers for the REST adapter."""

from __future__ import annotations

from fastapi import Header, Request

from security import Actor, get_actor_from_authorization


async def actor_dependency(authorization: str | None = Header(None, alias="Authorization")) -> Actor:
    return get_actor_from_authorization(authorization)


async def request_id_dependency(request: Request) -> str:
    """Return request_id from middleware state, falling back to header."""
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID", "")
