"""Agent load-balancing router for the cs-profit-engine (Task 8.3).

Routes incoming human-handoff conversations to the most appropriate
online agent based on:
- skill match (the agent must have a required skill, e.g. ``complaint``)
- user value tier (vip → senior agent preferred)
- current load (lower load-rate wins)
- agent_id lexicographic tie-breaker (deterministic across runs)

The router is the only stateful module in Task 8: agent registration /
load updates happen in-memory, do not persist, and are protected by a
``threading.Lock`` so concurrent registrations / updates / routes are
safe. The :data:`agent_router` singleton is the process-wide instance
the orchestrator consults at handoff time.

Per spec SubTask 8.3 — sort order when multiple agents match the
required skills:
    1. vip user → senior agent first
    2. lower load-rate (active / max_capacity) first
    3. agent_id ascending (tie-breaker for determinism)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentStatus:
    """In-memory record of an agent's identity and current load.

    ``active_conversations`` is mutable via :meth:`AgentRouter.update_load`;
    the other fields are set at registration time. ``skills`` is a set
    of free-form tags (e.g. ``{"complaint", "after_sales"}``) — the
    router matches by set intersection with the request's required skills.
    """

    agent_id: str
    seniority: str  # "senior" / "junior"
    active_conversations: int = 0
    max_capacity: int = 5
    skills: set[str] = field(default_factory=set)

    def load_rate(self) -> float:
        """Return ``active / max_capacity``, clamped to ``[0, 1]``.

        ``max_capacity == 0`` is treated as fully loaded (rate 1.0) so
        an agent configured with zero capacity is never routed to.
        """
        if self.max_capacity <= 0:
            return 1.0
        rate = self.active_conversations / self.max_capacity
        return max(0.0, min(1.0, rate))

    def has_capacity(self) -> bool:
        """Return ``True`` when the agent can take one more conversation."""
        return self.active_conversations < self.max_capacity


class AgentRouter:
    """In-memory agent load-balancing router.

    Thread-safe: every public method acquires the instance lock so
    concurrent ``register_agent`` / ``update_load`` / ``route`` calls
    from multiple orchestrator threads cannot corrupt the registry.

    The router does NOT persist state; on process restart the registry
    is empty and agents must re-register. This is intentional — agent
    availability is a runtime concern, not a durable configuration, and
    a stale persisted registry would route to agents that went offline
    ungracefully.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentStatus] = {}
        self._lock = threading.Lock()

    def register_agent(
        self,
        agent_id: str,
        seniority: str,
        skills: set[str] | None = None,
        max_capacity: int = 5,
    ) -> None:
        """Register or overwrite an agent.

        Re-registering an existing ``agent_id`` replaces its record (so
        an agent coming back online after a config change picks up the
        new seniority / skills / capacity). ``skills`` defaults to an
        empty set; ``max_capacity`` must be ``>= 0`` (``0`` means the
        agent is registered but cannot take new conversations — useful
        for draining).
        """
        with self._lock:
            self._agents[agent_id] = AgentStatus(
                agent_id=agent_id,
                seniority=seniority,
                active_conversations=0,
                max_capacity=max(0, int(max_capacity)),
                skills=set(skills) if skills else set(),
            )

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from the registry. No-op if not registered."""
        with self._lock:
            self._agents.pop(agent_id, None)

    def update_load(self, agent_id: str, active_conversations: int) -> None:
        """Update an agent's current active-conversation count.

        The count is the source of truth for the agent's load — the
        router does not infer it from observed traffic. ``active`` is
        clamped to ``[0, max_capacity]`` so a buggy update cannot push
        the load-rate above 1.0.
        """
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return
            clamped = max(0, int(active_conversations))
            if agent.max_capacity > 0:
                clamped = min(clamped, agent.max_capacity)
            agent.active_conversations = clamped

    def route(
        self,
        user_value_tier: str,
        required_skills: set[str] | None = None,
    ) -> str | None:
        """Pick the best agent for an incoming handoff.

        Filter:
        - Agent must have at least one of ``required_skills`` (set
          intersection). When ``required_skills`` is empty / ``None``,
          skill filtering is skipped.
        - Agent must have capacity (``active < max_capacity``).

        Sort (per spec SubTask 8.3):
        1. vip user → senior agents ranked first (senior=0, junior=1).
        2. Lower load-rate first.
        3. ``agent_id`` ascending (deterministic tie-breaker).

        Returns the chosen ``agent_id``, or ``None`` when no agent
        matches the skills or all matches are at capacity.
        """
        with self._lock:
            candidates: list[AgentStatus] = []
            required = set(required_skills) if required_skills else set()
            for agent in self._agents.values():
                if not agent.has_capacity():
                    continue
                if required and not (agent.skills & required):
                    continue
                candidates.append(agent)
            if not candidates:
                return None

            is_vip = (user_value_tier or "") == "vip"
            # Seniority sort key: when the user is vip, senior=0 / junior=1
            # (senior first). For non-vip users, seniority does not affect
            # the order — both get 0 so the load-rate decides. This
            # mirrors the spec: "vip 用户优先资深坐席".
            def seniority_key(agent: AgentStatus) -> int:
                if not is_vip:
                    return 0
                return 0 if agent.seniority == "senior" else 1

            candidates.sort(
                key=lambda a: (
                    seniority_key(a),
                    a.load_rate(),
                    a.agent_id,
                )
            )
            return candidates[0].agent_id

    def get_load_summary(self) -> list[dict[str, Any]]:
        """Return a snapshot of every registered agent's load.

        Sorted by ``agent_id`` asc for deterministic output. The
        snapshot is a deep copy (the ``skills`` set is materialised as
        a sorted list) so callers cannot mutate the router's internal
        state by editing the returned dicts.
        """
        with self._lock:
            rows: list[dict[str, Any]] = []
            for agent in self._agents.values():
                rows.append(
                    {
                        "agent_id": agent.agent_id,
                        "seniority": agent.seniority,
                        "active_conversations": agent.active_conversations,
                        "max_capacity": agent.max_capacity,
                        "load_rate": round(agent.load_rate(), 4),
                        "skills": sorted(agent.skills),
                    }
                )
            rows.sort(key=lambda r: r["agent_id"])
            return rows

    def reset_for_tests(self) -> None:
        """Clear the registry. Tests call this in setUp / tearDown so
        state does not leak across test cases.
        """
        with self._lock:
            self._agents.clear()


# Process-wide singleton. The orchestrator and the agent-assist pipeline
# consult this instance at handoff time. Tests use ``reset_for_tests``
# (or construct a fresh ``AgentRouter``) to isolate themselves.
agent_router = AgentRouter()
