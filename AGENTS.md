## Routing Directive (MANDATORY)

**ALL customer-facing interactions MUST go through `customer-service-orchestrator`. Never spawn sub-agents directly.**

This is architectural law (see ADR-0001: Single Orchestrator Entry Point). The orchestrator is the SOLE customer-facing agent. Sub-agents are internal-only and communicate exclusively through the orchestrator.

- `customer-service-orchestrator` — THE ONLY entry point for customer conversations. Use it for single-intent, multi-intent, new conversations, and ongoing conversations.
- All other agents are internal sub-agents. They are called BY the Orchestrator, never directly.
- If you see a customer message, your first action is always: invoke `customer-service-orchestrator`.

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues in [Customer-Service-Agent](https://github.com/K-boy-666/Customer-Service-Agent). See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Server startup

Before using this project, ensure the REST API server is running:

```bash
uvicorn order_api:app --reload --port 8000
```

MCP servers are launched automatically by Codex via `.Codex/mcp.json`.
