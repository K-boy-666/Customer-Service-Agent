"""Tests for peak-load dynamic scheduling and degradation (Task 7).

Covers SubTask 7.1 / 7.2 / 7.3 / 7.4:

- ``LoadMonitor`` acquire/release counter, load-percent calculation,
  degradation triggers (load > 80% OR queue-wait > 30s) and recovery.
- ``DegradationPolicy`` L0 priority, recommendation shedding, and the
  guarantee that L0 intents are still processed under degradation.
- Prometheus metric updates on acquire / release / set_degradation_active.

Tests call the ``LoadMonitor`` API directly — no HTTP / DB fixtures —
so the suite stays fast and deterministic. Each test resets the global
``load_monitor`` singleton in ``setUp`` / ``tearDown`` to avoid
cross-test bleed.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from prometheus_client import REGISTRY

from degradation import degradation_policy
from rate_limit import (
    LOAD_THRESHOLD_PERCENT,
    MAX_CONCURRENT_REQUESTS,
    QUEUE_WAIT_THRESHOLD_SECONDS,
    load_monitor,
)

# ---------------------------------------------------------------------------
# Helpers for reading Prometheus gauge/histogram values out of REGISTRY
# ---------------------------------------------------------------------------


def _gauge_value(metric_name: str) -> float:
    """Return the latest value of a labelless Gauge by metric name.

    Returns ``float('nan')`` if the metric family is not registered.
    """
    for metric_family in REGISTRY.collect():
        if metric_family.name == metric_name:
            for sample in metric_family.samples:
                # For a labelless Gauge, the sample name equals the
                # metric name (no _bucket / _sum / _count suffix).
                if sample.name == metric_name:
                    return float(sample.value)
    return float("nan")


def _histogram_count(metric_name: str) -> int:
    """Return the ``_count`` sample of a labelless Histogram."""
    for metric_family in REGISTRY.collect():
        if metric_family.name == metric_name:
            for sample in metric_family.samples:
                if sample.name == metric_name + "_count":
                    return int(sample.value)
    return 0


# ---------------------------------------------------------------------------
# SubTask 7.1 / 7.4 — LoadMonitor
# ---------------------------------------------------------------------------


class LoadMonitorTest(unittest.TestCase):
    """Acquire/release counter, load-percent, degradation triggers."""

    def setUp(self) -> None:
        load_monitor.reset_for_tests()

    def tearDown(self) -> None:
        load_monitor.reset_for_tests()

    def test_load_monitor_acquire_release(self) -> None:
        """acquire increments active; release decrements it (clamped at 0)."""
        self.assertEqual(load_monitor.active_count(), 0)
        load_monitor.acquire()
        load_monitor.acquire()
        self.assertEqual(load_monitor.active_count(), 2)
        load_monitor.release(wait_time=0.1)
        self.assertEqual(load_monitor.active_count(), 1)
        load_monitor.release(wait_time=0.2)
        self.assertEqual(load_monitor.active_count(), 0)
        # release below 0 must clamp at 0 (defensive — never negative).
        load_monitor.release(wait_time=0.0)
        self.assertEqual(load_monitor.active_count(), 0)

    def test_load_monitor_load_percent(self) -> None:
        """load_percent = active / max * 100."""
        half = MAX_CONCURRENT_REQUESTS // 2
        for _ in range(half):
            load_monitor.acquire()
        expected = (half / MAX_CONCURRENT_REQUESTS) * 100.0
        self.assertAlmostEqual(load_monitor.current_load_percent(), expected, places=2)

    def test_load_monitor_degradation_triggered_by_load(self) -> None:
        """Load strictly greater than 80% triggers ``is_degradation_needed``."""
        # Push active count just past the threshold (80% of 100 = 80;
        # need 81 to be strictly greater than 80).
        threshold_count = int(MAX_CONCURRENT_REQUESTS * LOAD_THRESHOLD_PERCENT / 100.0)
        for _ in range(threshold_count + 1):
            load_monitor.acquire()
        self.assertGreater(load_monitor.current_load_percent(), LOAD_THRESHOLD_PERCENT)
        self.assertTrue(load_monitor.is_degradation_needed())

    def test_load_monitor_degradation_triggered_by_queue_wait(self) -> None:
        """Average queue wait > 30s triggers ``is_degradation_needed``."""
        # A single slow request is enough: avg = 35s > 30s threshold.
        load_monitor.acquire()
        load_monitor.release(wait_time=QUEUE_WAIT_THRESHOLD_SECONDS + 5.0)
        self.assertGreater(
            load_monitor.recent_queue_wait_seconds(),
            QUEUE_WAIT_THRESHOLD_SECONDS,
        )
        self.assertTrue(load_monitor.is_degradation_needed())

    def test_load_monitor_degradation_recovered(self) -> None:
        """After load drops below threshold, ``is_degradation_needed`` is False."""
        threshold_count = int(MAX_CONCURRENT_REQUESTS * LOAD_THRESHOLD_PERCENT / 100.0)
        # Trigger degradation via load.
        acquired = threshold_count + 5
        for _ in range(acquired):
            load_monitor.acquire()
        self.assertTrue(load_monitor.is_degradation_needed())
        # Drain everything with zero wait times; load returns to 0 and
        # the recent-wait average drops below the 30s threshold.
        for _ in range(acquired):
            load_monitor.release(wait_time=0.0)
        self.assertEqual(load_monitor.active_count(), 0)
        self.assertFalse(load_monitor.is_degradation_needed())


# ---------------------------------------------------------------------------
# SubTask 7.2 / 7.4 — DegradationPolicy
# ---------------------------------------------------------------------------


class DegradationPolicyTest(unittest.TestCase):
    """Per-intent priority bands and shedding behaviour."""

    def setUp(self) -> None:
        load_monitor.reset_for_tests()

    def tearDown(self) -> None:
        load_monitor.reset_for_tests()

    def test_degradation_policy_skips_recommendation(self) -> None:
        """Recommendation is skipped only when degradation is active."""
        load_monitor.set_degradation_active(False)
        self.assertFalse(degradation_policy.should_skip_recommendation())
        load_monitor.set_degradation_active(True)
        self.assertTrue(degradation_policy.should_skip_recommendation())

    def test_degradation_policy_priority_for_l0(self) -> None:
        """L0 intents (order_inquiry, consultation) get the highest priority."""
        self.assertEqual(degradation_policy.priority_for_intent("order_inquiry"), 0)
        self.assertEqual(degradation_policy.priority_for_intent("consultation"), 0)

    def test_degradation_policy_priority_for_recommendation(self) -> None:
        """Recommendation has the lowest priority (3)."""
        self.assertEqual(degradation_policy.priority_for_intent("recommendation"), 3)
        # Every customer-facing intent must outrank recommendation.
        for intent in ("order_inquiry", "consultation", "after_sales", "work_order", "complaint"):
            self.assertLess(
                degradation_policy.priority_for_intent(intent),
                degradation_policy.priority_for_intent("recommendation"),
                msg=f"{intent} should outrank recommendation",
            )

    def test_degradation_policy_should_process_l0(self) -> None:
        """L0 intents are always processed even under degradation."""
        load_monitor.set_degradation_active(True)
        self.assertTrue(degradation_policy.should_process("order_inquiry"))
        self.assertTrue(degradation_policy.should_process("consultation"))

    def test_degradation_policy_sheds_recommendation_under_load(self) -> None:
        """Profit-engine internal intents are shed when degradation is on."""
        load_monitor.set_degradation_active(True)
        self.assertFalse(degradation_policy.should_process("recommendation"))
        self.assertFalse(degradation_policy.should_process("analytics"))
        # When degradation is off, everything is processed.
        load_monitor.set_degradation_active(False)
        self.assertTrue(degradation_policy.should_process("recommendation"))
        self.assertTrue(degradation_policy.should_process("analytics"))


# ---------------------------------------------------------------------------
# SubTask 7.3 / 7.4 — Prometheus metrics update on acquire/release
# ---------------------------------------------------------------------------


class PeakLoadMetricsTest(unittest.TestCase):
    """Prometheus gauges/histogram reflect LoadMonitor state."""

    def setUp(self) -> None:
        load_monitor.reset_for_tests()

    def tearDown(self) -> None:
        load_monitor.reset_for_tests()

    def test_metrics_updated_on_acquire_release(self) -> None:
        """cs_active_requests follows acquire/release; histogram observes."""
        baseline = _gauge_value("cs_active_requests")

        load_monitor.acquire()
        self.assertEqual(_gauge_value("cs_active_requests"), baseline + 1.0)

        # release observes the wait_time on the histogram and decrements
        # the active gauge back to baseline.
        count_before = _histogram_count("cs_queue_wait_seconds")
        load_monitor.release(wait_time=2.5)
        self.assertEqual(_gauge_value("cs_active_requests"), baseline)
        count_after = _histogram_count("cs_queue_wait_seconds")
        self.assertGreater(count_after, count_before)

    def test_metrics_degradation_flag_reflected(self) -> None:
        """cs_degradation_active mirrors set_degradation_active."""
        load_monitor.set_degradation_active(True)
        self.assertEqual(_gauge_value("cs_degradation_active"), 1.0)
        load_monitor.set_degradation_active(False)
        self.assertEqual(_gauge_value("cs_degradation_active"), 0.0)

    def test_metrics_load_percent_refreshed_by_is_degradation_needed(self) -> None:
        """is_degradation_needed refreshes cs_load_percent to current load."""
        half = MAX_CONCURRENT_REQUESTS // 2
        for _ in range(half):
            load_monitor.acquire()
        load_monitor.is_degradation_needed()
        expected_pct = (half / MAX_CONCURRENT_REQUESTS) * 100.0
        self.assertAlmostEqual(
            _gauge_value("cs_load_percent"),
            expected_pct,
            places=2,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
