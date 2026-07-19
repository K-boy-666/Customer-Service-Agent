"""Prometheus metrics using the official prometheus_client library.

Replaces the custom MetricsRegistry with standard Counter/Gauge/Histogram
types. Supports multiprocess mode via PROMETHEUS_MULTIPROC_DIR for
multi-worker uvicorn deployments.
"""

from __future__ import annotations

import time
from typing import Literal

from prometheus_client import Counter, Gauge, Histogram

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


# --- Peak-load dynamic scheduling metrics (Task 7) ---
# These track global load and degradation state so operators can alert
# when the orchestrator is shedding optional profit-engine work.
# Histogram bucket boundaries span sub-millisecond waits up to 60s so
# both healthy (≤1s) and degraded (>30s) regimes are visible.
cs_queue_wait_seconds = Histogram(
    "cs_queue_wait_seconds",
    "Customer service request queue wait time in seconds",
    buckets=(0.01, 0.1, 0.5, 1, 5, 10, 30, 60),
)

# 0/1 flag — whether degradation mode is currently engaged.
cs_degradation_active = Gauge(
    "cs_degradation_active",
    "Whether degradation mode is active (1=yes, 0=no)",
)

# Instantaneous in-flight request count (mirrors LoadMonitor._active).
cs_active_requests = Gauge(
    "cs_active_requests",
    "Current number of active customer service requests",
)

# Instantaneous load percentage (active / max * 100).
cs_load_percent = Gauge(
    "cs_load_percent",
    "Current load percentage (active/max*100)",
)


def record_request(route: str, start_time: float) -> None:
    """Record a request's duration from a perf_counter start time."""
    REQUEST_LATENCY.labels(route=route).observe(time.perf_counter() - start_time)


# --- Profit dashboard metrics (Task 9) ---
# Histogram for the three v1 dashboard endpoints. Bucket boundaries span
# 10ms (fast in-memory queries) up to 10s so operators can spot SLA
# regressions well beyond the 2s budget.
dashboard_latency_seconds = Histogram(
    "dashboard_latency_seconds",
    "Profit dashboard API latency in seconds",
    ["endpoint"],
    buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10),
)

# Cumulative counter for attributed revenue observed via the API. Labelled
# by attribution model so operators can compare revenue exposure across
# first_touch / last_touch / linear / time_decay.
attribution_revenue_total = Counter(
    "attribution_revenue_total",
    "Total attributed revenue in CNY",
    ["model"],
)
