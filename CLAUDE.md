## Session Boot

Do this before starting work on a user request:

1. Read `progress.md` to identify active work and blockers.
2. Read the latest `## Session` block in `session-handoff.md`.
3. Run the cross-platform init check:
   - Windows command prompt or PowerShell: `./init.cmd --check-only --skip-tests`
   - POSIX shell: `bash init.sh --check-only --skip-tests`
4. Mention any relevant startup warnings naturally in your first response.

Before the conversation ends:

1. Append a `## Session` block to `session-handoff.md`.
2. Update `progress.md` if feature state or verification evidence changed.

## Fast Agent Workflow

- Prefer `C:\Users\JOSAP\scoop\shims\rg.exe` for searches. In an existing PowerShell session, prepend `$env:USERPROFILE\scoop\shims` to `PATH` before running `rg`.
- Do not run unrestricted `Get-ChildItem -Recurse` from the repository root. If PowerShell fallback search is needed, exclude `.venv`, `.git`, `.pytest_cache`, and `__pycache__`.
- When testing Chinese customer messages in PowerShell, use UTF-8 file input or Unicode escapes. Avoid inline Chinese here-strings that can be corrupted by the console code page.
- When diagnosing customer requests, reuse MCP or the same Python process where practical. Avoid one cold Python/FastMCP startup per message.
- For one-off customer-message diagnostics, call `orchestrator_mcp_tool.handle_customer_message_tool` directly, or reuse an already-running MCP server. Import `server_customer.py` only when testing or running the full MCP server.

## Routing Directive (Mandatory)

All customer-facing interactions must go through `customer-service-orchestrator`.

This is architectural law from ADR-0001. The orchestrator is the sole customer-facing agent. Sub-agents are internal modules and communicate only through the orchestrator.

- `customer-service-orchestrator`: the only entry point for customer conversations, including single-intent, multi-intent, new, and ongoing conversations.
- `customer-service-dispatcher`: internal intent engine only.
- `order-inquiry-agent`, `consultation-agent`, `after-sales-agent`, `work-order-agent`, `complaint-agent`, and `human-handoff-agent`: internal sub-agents only.
- `customer-service` MCP exposes `handle_customer_message`; use that for customer text.
- `order-server` MCP is read-only internal support and must not be used as a customer-facing bypass.

## Dispatcher Extension Contract

The current runtime uses deterministic intent routing so tests do not require a live LLM. Any future LLM dispatcher must preserve the same interface:

- Input: customer context and task, following ADR-0002.
- Output: structured intents with `intent`, `confidence`, `suggested_agent`, and `reason`.
- Safety: L3 handoff wins before all other intents; L2 complaint routes to complaint plus work-order recording.
- Permissions: never route L1 writes to L0 or L2 agents.
- Tests: existing orchestrator E2E and security tests must continue to pass.

## Agent Skills

### Issue Tracker

Issues are tracked as GitHub Issues in Customer-Service-Agent. See `docs/agents/issue-tracker.md`.

### Triage Labels

Default label vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain Docs

Single-context domain docs live in `CONTEXT.md` and `docs/adr/`.

## Harness File Index

Load files on demand only when the task matches the purpose below.

| File | What it contains | When to read |
|------|------------------|--------------|
| `progress.md` | Active feature, recent completions, blockers, verification state | Every session start |
| `session-handoff.md` | Last session context and resume point | Every session start |
| `feature_list.json` | Features, statuses, dependencies, done criteria | When scoping work or checking completion |
| `init.cmd` / `init.ps1` / `init.sh` | Platform wrappers for init checks | Every session start |
| `scripts/harness/init_check.py` | 6-stage verification implementation | When debugging startup checks |
| `docs/verification-workflow.md` | Correctness/security/perf/coverage/regression gates | Before marking a feature done |
| `docs/patterns-applied.md` | Harness patterns mapped to this project | When designing infrastructure |
| `docs/memory-progress-handoff.md` | Cross-session memory and handoff architecture | When transferring work |
| `docs/standardized-sequences.md` | Ops sequences and runbooks | For ops/onboarding tasks |
| `scripts/harness/validate-harness.mjs` | Harness audit | Before merge or release |
| `scripts/harness/run-benchmark.mjs` | Structural benchmark | Release gate |
| `scripts/harness/render-assessment-html.mjs` | HTML assessment report | Review/demo |
| `scripts/harness/create-harness.mjs` | Harness scaffolder | Bootstrapping a new repo |

## Server Startup

Before using REST-backed MCP tools, ensure the REST API server is running:

```bash
uvicorn order_api:app --reload --port 8000
```

MCP servers are launched automatically from `.claude/mcp.json`.
