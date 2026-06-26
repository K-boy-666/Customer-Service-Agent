## Session boot (DO THIS FIRST — before responding to ANY user request)

When you start, execute these steps internally before your first response. The user does NOT need to ask — this is your own startup routine:

1. **Read** `progress.md` — find the active feature and any blockers.
2. **Read** `session-handoff.md` — find the latest `## Session` block and its "Recommended next action".
3. **Run** `bash init.sh --check-only --skip-tests` — verify the environment is ready.
4. **Incorporate** the results into your first response to the user. If the user asked a task question, weave the context in naturally (e.g., "在开始之前，当前项目状态是：..."). If the user just said "hi" or has no task yet, declare the full context explicitly.

Before the conversation ends, always:
1. **Append** a `## Session` block to `session-handoff.md`.
2. **Update** `progress.md` if feature states changed.

## Routing Directive (MANDATORY)

**ALL customer-facing interactions MUST go through `customer-service-orchestrator`. Never spawn sub-agents directly.**

This is architectural law (see ADR-0001: Single Orchestrator Entry Point). The orchestrator is the SOLE customer-facing agent. Sub-agents are internal-only and communicate exclusively through the orchestrator.

- `customer-service-orchestrator` — THE ONLY entry point for customer conversations. Use it for single-intent, multi-intent, new conversations, and ongoing conversations.
- All other agents are internal sub-agents. They are called BY the Orchestrator, never directly.
- If you see a customer message, route it through `customer-service-orchestrator`.

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues in [Customer-Service-Agent](https://github.com/K-boy-666/Customer-Service-Agent). See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Harness file index

> Load on demand — only read when the task matches the "When to read" column.

| File | What it contains | When to read |
|------|-----------------|-------------|
| `progress.md` | Active feature, recent completions, blockers, verification state | Every session start (automatic via boot sequence) |
| `session-handoff.md` | Last session's context + resume point | Every session start (automatic via boot sequence) |
| `feature_list.json` | All features, statuses, dependencies, done criteria (JSON Schema validated) | When scoping new work or checking feature state |
| `init.sh` | 6-stage verification: Python→deps→DB→API→MCP→tests | Every session start (automatic via boot sequence) |
| `docs/verification-workflow.md` | 5-dimension scoring: Correctness/Security/Perf/Coverage/Regression with 3 gates | Before marking a feature done; before merge |
| `docs/patterns-applied.md` | 7 harness patterns (memory, skills, context, tools, multi-agent, lifecycle, gotchas) mapped to this project | When designing new infrastructure or debugging harness issues |
| `docs/memory-progress-handoff.md` | Cross-session memory architecture, progress state machine, agent-to-agent handoff protocol | When transferring work between agents or debugging session issues |
| `docs/standardized-sequences.md` | Project init, background tasks, session boot, emergency runbooks, quick reference card | When performing ops tasks or onboarding |
| `scripts/harness/validate-harness.mjs` | 5-subsystem audit with weighted scoring → JSON/console output | Before merge or release (`--score` for CI) |
| `scripts/harness/run-benchmark.mjs` | Structural benchmark: agent fleet, test coverage, MCP, docs, git health | Release gate (`--html` for report) |
| `scripts/harness/render-assessment-html.mjs` | Dark-theme HTML assessment report with score cards + recommendations | Team review or stakeholder demo |
| `scripts/harness/create-harness.mjs` | Scaffold a minimal harness into any project (`--target`, `--force`, `--dry-run`) | When bootstrapping harness for a new repo |

## Server startup

Before using this project, ensure the REST API server is running:

```bash
uvicorn order_api:app --reload --port 8000
```

MCP servers are launched automatically by Claude Code via `.claude/mcp.json`.
