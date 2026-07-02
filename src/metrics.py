"""Prometheus metrics using the official prometheus_client library.

Replaces the custom MetricsRegistry with standard Counter/Gauge/Histogram
types. Supports multiprocess mode via PROMETHEUS_MULTIPROC_DIR for
multi-worker uvicorn deployments.
"""

from __future__ import annotations

import time
from typing import Literal

from prometheus_client import Gauge, Histogram

# --- Request latency histogram (replaces custom MetricsRegistry) ---
# Keep the same bucket boundaries as the original hand-written implementation.
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds.",
    ["route"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# --- DB-count gauges (query current state at scrape time) ---
# All are Gauge types since they reflect current DB state, not monotonic
# increases.  multiprocess_mode='mostrecent' ensures the latest scrape value
# wins when multiple workers have written stale values.
_MULTIPROC_MODE: Literal["mostrecent"] = "mostrecent"

CONVERSATIONS_TOTAL = Gauge(
    "customer_service_conversations_total",
    "Total orchestrator conversations recorded.",
    multiprocess_mode=_MULTIPROC_MODE,
)
HANDOFFS_TOTAL = Gauge(
    "customer_service_handoffs_total",
    "Total conversations that needed human handoff.",
    multiprocess_mode=_MULTIPROC_MODE,
)
TICKETS_TOTAL = Gauge(
    "customer_service_tickets_total",
    "Total tickets.",
    multiprocess_mode=_MULTIPROC_MODE,
)
RETURNS_TOTAL = Gauge(
    "customer_service_returns_total",
    "Total return requests.",
    multiprocess_mode=_MULTIPROC_MODE,
)
SURVEYS_TOTAL = Gauge(
    "customer_service_surveys_total",
    "Total satisfaction surveys.",
    multiprocess_mode=_MULTIPROC_MODE,
)


def record_request(route: str, start_time: float) -> None:
    """Record a request's duration from a perf_counter start time."""
    REQUEST_LATENCY.labels(route=route).observe(time.perf_counter() - start_time)
