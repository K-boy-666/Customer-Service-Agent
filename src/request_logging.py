"""Pure ASGI middleware for structured request logging + contextvars binding.

Replaces the former ``metrics_middleware`` with unified context binding:
- Binds request_id, method, path to structlog contextvars before request
- Captures status_code and duration after request
- Records metrics (replaces metrics_middleware)
- Logs a structured request summary

Uses a pure ASGI middleware (not BaseHTTPMiddleware) for reliable
contextvars propagation across the sync/async boundary.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from metrics import record_request

logger = structlog.get_logger("request")


class StructuredRequestLoggingMiddleware:
    """Pure ASGI middleware — no BaseHTTPMiddleware child-task overhead."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        structlog.contextvars.clear_contextvars()

        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex
        method = scope.get("method", "")
        path = scope.get("path", "")

        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=method,
            path=path,
        )

        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            structlog.contextvars.bind_contextvars(
                status_code=status_code,
                duration_ms=round(duration * 1000, 2),
            )
            logger.info(
                "http_request",
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=round(duration * 1000, 2),
            )
            record_request(path, start)
