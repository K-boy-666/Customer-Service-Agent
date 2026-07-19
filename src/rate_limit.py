"""slowapi rate limiter setup with env-driven configuration.

Task 7.1 extends this module with :class:`LoadMonitor` — a global,
thread-safe tracker for in-flight customer-service requests and recent
queue-wait times. The monitor feeds both the degradation policy
(:mod:`degradation`) and the Prometheus gauges/histogram declared in
:mod:`metrics`. It does NOT gate request admission; the orchestrator
calls ``acquire`` / ``release`` around each customer message so the
metrics and degradation flag stay accurate.
"""

from __future__ import annotations

import os
import threading
from collections import deque

from slowapi import Limiter
from slowapi.util import get_remote_address

import config as runtime_config
from metrics import (
    cs_active_requests,
    cs_degradation_active,
    cs_load_percent,
    cs_queue_wait_seconds,
)

_cfg = runtime_config.load_runtime_config()

LIMIT_OTP = _cfg.rate_limit_otp
LIMIT_ORCHESTRATOR = _cfg.rate_limit_orchestrator
LIMIT_WRITE = _cfg.rate_limit_write
LIMIT_READ = _cfg.rate_limit_read

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_cfg.rate_limit_storage_uri,
    enabled=_cfg.rate_limit_enabled,
)


# ---------------------------------------------------------------------------
# Task 7.1 — global load monitor for peak-load dynamic scheduling
# ---------------------------------------------------------------------------

# Threshold configuration (env-overridable). Defaults match the spec:
#   MAX_CONCURRENT_REQUESTS        = 100
#   QUEUE_WAIT_THRESHOLD_SECONDS   = 30
#   LOAD_THRESHOLD_PERCENT         = 80
MAX_CONCURRENT_REQUESTS = int(os.getenv("CS_MAX_CONCURRENT", "100"))
QUEUE_WAIT_THRESHOLD_SECONDS = float(os.getenv("CS_QUEUE_WAIT_THRESHOLD", "30"))
LOAD_THRESHOLD_PERCENT = float(os.getenv("CS_LOAD_THRESHOLD", "80"))


class LoadMonitor:
    """Global load monitor for peak-load degradation decisions.

    Tracks the number of in-flight customer-service requests and a
    sliding window of the last 1000 request wait times.
    :meth:`is_degradation_needed` returns ``True`` when either the
    active-request ratio exceeds :data:`LOAD_THRESHOLD_PERCENT` or the
    average recent wait time exceeds
    :data:`QUEUE_WAIT_THRESHOLD_SECONDS`.

    All shared state is guarded by a single :class:`threading.Lock`.
    Prometheus metric updates happen OUTSIDE the lock to keep the
    critical section short and avoid any re-entrancy risk from the
    metrics library (which has its own internal locking).
    """

    def __init__(self) -> None:
        self._active = 0
        self._lock = threading.Lock()
        self._wait_times: deque[float] = deque(maxlen=1000)
        self._degradation_active = False

    def acquire(self) -> float:
        """Mark a request as started; return the wait time (always 0.0).

        The orchestrator does not run an explicit request queue today,
        so ``acquire`` only increments the in-flight counter. Callers
        that measure queue wait externally pass that value to
        :meth:`release`.

        Updates the ``cs_active_requests`` Prometheus gauge.
        """
        with self._lock:
            self._active += 1
            active = self._active
        cs_active_requests.set(active)
        return 0.0

    def release(self, wait_time: float) -> None:
        """Mark a request as finished and record its queue wait time.

        Updates both ``cs_active_requests`` (decremented) and the
        ``cs_queue_wait_seconds`` histogram (observes ``wait_time``).
        ``wait_time`` should be the seconds the request spent waiting
        in queue before being processed; pass ``0.0`` when no queueing
        occurred.
        """
        with self._lock:
            self._active = max(0, self._active - 1)
            active = self._active
            self._wait_times.append(float(wait_time))
        cs_active_requests.set(active)
        cs_queue_wait_seconds.observe(float(wait_time))

    def active_count(self) -> int:
        """Current in-flight request count (read-only, for tests/metrics)."""
        with self._lock:
            return self._active

    def current_load_percent(self) -> float:
        """Active / max * 100. Returns 0.0 when max is non-positive."""
        with self._lock:
            active = self._active
        if MAX_CONCURRENT_REQUESTS <= 0:
            return 0.0
        return (active / MAX_CONCURRENT_REQUESTS) * 100.0

    def recent_queue_wait_seconds(self) -> float:
        """Average wait time over the last 1000 recorded requests.

        Returns 0.0 when no requests have been recorded yet.
        """
        with self._lock:
            if not self._wait_times:
                return 0.0
            return sum(self._wait_times) / len(self._wait_times)

    def is_degradation_needed(self) -> bool:
        """True when load > 80% or average queue wait > 30s.

        Refreshes the ``cs_load_percent`` and ``cs_degradation_active``
        Prometheus gauges as a side effect so a scrape after this call
        reflects the latest state. Does NOT toggle
        :attr:`_degradation_active` — that is the caller's responsibility
        via :meth:`set_degradation_active`.
        """
        load_pct = self.current_load_percent()
        avg_wait = self.recent_queue_wait_seconds()
        needed = (
            load_pct > LOAD_THRESHOLD_PERCENT
            or avg_wait > QUEUE_WAIT_THRESHOLD_SECONDS
        )
        cs_load_percent.set(load_pct)
        with self._lock:
            cs_degradation_active.set(1 if self._degradation_active else 0)
        return needed

    def set_degradation_active(self, active: bool) -> None:
        """Mark degradation as on/off.

        Updates the ``cs_degradation_active`` Prometheus gauge to match.
        The orchestrator's peak-load controller calls this after
        consulting :meth:`is_degradation_needed`.
        """
        with self._lock:
            self._degradation_active = bool(active)
            flag = self._degradation_active
        cs_degradation_active.set(1 if flag else 0)

    def is_degradation_active(self) -> bool:
        """Whether degradation is currently active."""
        with self._lock:
            return self._degradation_active

    def reset_for_tests(self) -> None:
        """Reset all internal state and the related Prometheus gauges.

        Tests call this in ``setUp`` to avoid cross-test bleed. The
        ``cs_queue_wait_seconds`` histogram cannot be reset (Prometheus
        histograms are append-only), so tests must not assert on its
        absolute count.
        """
        with self._lock:
            self._active = 0
            self._wait_times.clear()
            self._degradation_active = False
        cs_active_requests.set(0)
        cs_load_percent.set(0.0)
        cs_degradation_active.set(0)


# Module-level singleton. The orchestrator and degradation policy share
# this instance so a single source of truth tracks global load.
load_monitor = LoadMonitor()
