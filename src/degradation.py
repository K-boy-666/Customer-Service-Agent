"""Peak-load degradation policy for the cs-profit-engine (Task 7.2).

Thin layer over :data:`rate_limit.load_monitor` that decides which
optional profit-engine work should be skipped or deferred while the
system is under peak load. The policy is intentionally stateless
beyond the monitor — it can be unit-tested without HTTP / DB fixtures
and the orchestrator can consult it inline at every hook site.

Priority bands (lower number = higher priority, matches the spec):
    L0 (read-only, customer-facing):  order_inquiry, consultation, satisfaction
    L1 (write, customer-facing):      after_sales, work_order
    L2 (escalation, conversation):    complaint, human_handoff
    profit-engine internal:           recommendation, analytics  (shed first)

L0/L1/L2 customer-facing intents are ALWAYS processed — even under
degradation we never silence a customer. Only the profit-engine
internal intents (recommendation / analytics) are candidates for
shedding; ``should_process`` returns ``False`` for them when
degradation is active.
"""

from __future__ import annotations

from rate_limit import load_monitor

# Intent → priority band. Unknown intents default to 3 (lowest) so any
# new profit-engine-side intent is opt-in for processing under load.
_INTENT_PRIORITY: dict[str, int] = {
    "order_inquiry": 0,
    "consultation": 0,
    "satisfaction": 0,
    "after_sales": 1,
    "work_order": 1,
    "complaint": 2,
    "human_handoff": 2,
    "recommendation": 3,
    "analytics": 3,
}

# Intents that are ALWAYS processed (L0 + L1 + L2 customer-facing).
# Profit-engine internal intents (priority 3) are the only ones shed
# under degradation.
_ALWAYS_PROCESS: frozenset[str] = frozenset(
    {
        "order_inquiry",
        "consultation",
        "satisfaction",
        "after_sales",
        "work_order",
        "complaint",
        "human_handoff",
    }
)


class DegradationPolicy:
    """Decide per-feature behaviour under peak load.

    Every method delegates to the global :data:`rate_limit.load_monitor`
    so the policy tracks the live load state without owning any state
    of its own.
    """

    def should_skip_recommendation(self) -> bool:
        """True when recommendation generation should be skipped.

        Recommendations are the most expensive profit-engine work
        (synchronous, 2s SLA) and the lowest customer-facing priority,
        so they are the first thing to shed under load.
        """
        return load_monitor.is_degradation_active()

    def should_use_profile_cache(self) -> bool:
        """True when profile queries should fall back to a cached value.

        The profile service does not yet expose a cache; this flag lets
        a future caller short-circuit a DB hit when degradation is on.
        """
        return load_monitor.is_degradation_active()

    def should_delay_work_order(self) -> bool:
        """True when non-urgent work orders should be deferred.

        Urgent work orders (complaints, escalations) are never deferred
        — see :meth:`should_process` for the L2 routing guarantee. This
        flag targets only routine ticket creation.
        """
        return load_monitor.is_degradation_active()

    def priority_for_intent(self, intent: str) -> int:
        """Lower number = higher priority. Unknown intents default to 3."""
        return _INTENT_PRIORITY.get(intent, 3)

    def should_process(self, intent: str) -> bool:
        """Whether ``intent`` should be processed under the current load.

        L0/L1/L2 customer-facing intents are ALWAYS processed; the
        profit-engine internal intents (recommendation / analytics)
        are shed when degradation is active.
        """
        if intent in _ALWAYS_PROCESS:
            return True
        if load_monitor.is_degradation_active():
            return self.priority_for_intent(intent) < 3
        return True


# Module-level singleton — the orchestrator and profit-engine hooks
# consult this instance at every hook site.
degradation_policy = DegradationPolicy()
