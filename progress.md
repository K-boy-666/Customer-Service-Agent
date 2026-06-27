# Progress — 客服智能体 2.0

> Last updated: 2026-06-27
> Active branch: `main`

## Current feature

None active — all planned features are `done`. See `feature_list.json` for the full inventory.

## Recent completions

| Date | Feature | Commit | Evidence |
|------|---------|--------|----------|
| 2026-06-26 | Merge: customer agent P0/P1 hardening | `d620738` | orchestrator e2e + security tests pass |
| 2026-06-25 | Customer verification + analytics telemetry hardening | `c0badab` | ADR-0005 accepted, tests/test_daily_analytics.py |
| 2026-06-25 | Daily analytics subagent | `c7a72d9` | data-analysis-agent provisioned |
| 2026-06-25 | RAG support + engineering skills | `1b57b65` | tests/test_rag_faq.py passing |
| 2026-06-24 | MCP env config fix + order API bugfixes | `80f7dbf` | MCP servers boot cleanly |
| 2026-06-24 | Production security + data layer | `90ef957` | Security controls tested |
| 2026-06-24 | Multi-agent orchestration system, MCP, tests, memory | `b21b5d0` | Full agent fleet operational |

## Active blockers

_None._

## Planned next

| Priority | Task | Depends on |
|----------|------|------------|
| P1 | Production deployment hardening (MySQL migration, secrets, monitoring) | None |
| P2 | Multi-agent concurrency + load testing | P0 features stable |

## Verification state

```
$ bash init.sh
✔ Python 3.10+ … OK
✔ Dependencies (uv sync) … OK
✔ Database (alembic upgrade head) … OK
✔ order_api :8000 health … OK
✔ MCP server boot smoke … OK
✔ pytest … 45 passed
```

## Agent memory index

- `.claude/agent-memory/customer-service-orchestrator/` — dispatch patterns, de-escalation phrases
- `.claude/agent-memory/customer-service-dispatcher/` — intent patterns, FAQ edge cases
- `memory/` — project-scoped user/feedback/project/reference memories

## Notes

- The CL entrypoint is `customer-service-orchestrator` per ADR-0001. Never bypass.
- MCP servers start automatically via `.claude/mcp.json`. No manual `uvicorn` needed.
- All sub-agents follow ADR-0002 `【客户上下文】+【任务】` → `【处理结果】+【客户回复】+【内部备注】` protocol.
- Tests use SQLite; production targets MySQL via Alembic.

## Risk hardening verification - 2026-06-26

- Cross-platform init entrypoints added: `init.cmd`, `init.ps1`, `init.sh` -> `scripts/harness/init_check.py`.
- Customer-facing MCP path remains `customer-service.handle_customer_message`; `order-server` no longer carries or forwards `IDENTITY_VERIFICATION`.
- Key governance files are ASCII/UTF-8 without BOM, covered by `tests/test_harness_risk_controls.py`.
- Dev JWT default/test secrets are >=32 bytes; pytest warning output is clean.
- Verification evidence:
  - `uv run pytest tests/ -q` -> 37 passed, 14 subtests passed, 0 warnings.
  - `node scripts/harness/validate-harness.mjs` with bundled Node -> weighted total 100/100, all checks passed.
  - `./init.cmd --check-only --skip-tests` -> no failures; warnings only for REST API not running and tests intentionally skipped.
  - `./init.cmd` ran successfully earlier in this session after adding the entrypoint -> tests passed; later rerun was blocked by platform usage limit, not by project failure.

## Agent reliability hardening verification - 2026-06-27

- MCP `handle_customer_message` now classifies only auth/permission failures as `denied`; business/runtime failures return `failed` without claiming that no writes happened.
- Order shipment lookup now treats missing shipment records as a partial business result after order lookup, so prior successful actions in a multi-intent request are still surfaced.
- Orchestrator write fan-out now derives per-operation idempotency keys from the caller key plus operation payload, preventing low-score and complaint tickets from colliding.
- Conversation state now remembers recent `customer_id` and `order_id` by `conversation_id`, allowing follow-up turns such as `I want to return it` to reuse context without storing raw messages.
- Verification evidence:
  - `.\.venv\Scripts\python.exe -m pytest tests\test_orchestrator_e2e.py -q -p no:cacheprovider` -> 7 passed.
  - `.\.venv\Scripts\python.exe -m pytest tests\test_harness_risk_controls.py tests\test_rag_faq.py tests\test_rag_customer_scenarios.py tests\test_security_controls.py -q -p no:cacheprovider` -> 23 passed, 14 subtests passed.
  - `.\init.cmd --check-only --skip-tests` -> no failures; warnings only for REST API not running and tests intentionally skipped.
  - `$env:Path="$env:USERPROFILE\scoop\shims;$env:Path"; rg --version` -> ripgrep 15.1.0.
- Verification caveats:
  - Full `.venv` pytest currently reaches 41 passed, 14 subtests passed, then fails in `DailyAnalyticsTest.test_cli_writes_markdown_report` due Windows temp-directory permission/cleanup errors.
  - `uv run pytest ...` is blocked by Windows permission errors in the uv cache/build temp directories, even with `UV_CACHE_DIR` moved into the workspace.

## Cold-start optimization verification - 2026-06-27

- REST-only FastAPI dependencies were moved out of `security.py` into `src/api_dependencies.py`, so the local Orchestrator tool path no longer imports FastAPI.
- Core runtime/service modules now use Starlette `HTTPException`/status constants while `order_api.py` remains the FastAPI adapter.
- Fast Agent Workflow now documents that one-off customer-message diagnostics should use `orchestrator_mcp_tool.handle_customer_message_tool` or a reused MCP process, not a fresh full `server_customer.py` import.
- Verification evidence:
  - `python -X importtime -c "import orchestrator_mcp_tool"` -> no `fastapi` import in filtered output; `orchestrator_mcp_tool` cumulative import about 0.82s in importtime output.
  - `Measure-Command { python -c "import orchestrator_mcp_tool" }` -> 1.02s cold import; `Measure-Command { python -c "import server_customer" }` -> 3.64s full MCP import.
  - Lightweight complaint probe via `orchestrator_mcp_tool.handle_customer_message_tool` -> 0.0302s handler time, `status=denied`, `error=missing_identity_verification`.
  - Valid verification complaint probe -> `status=needs-human`, `emotional_level=L2`, dispatched complaint/work-order/after-sales/order agents, created 1 ticket.
  - `.\.venv\Scripts\python.exe -m pytest tests\test_orchestrator_e2e.py -q -p no:cacheprovider` -> 7 passed.
  - `.\.venv\Scripts\python.exe -m pytest tests\test_security_controls.py tests\test_harness_risk_controls.py -q -p no:cacheprovider` -> 17 passed.
  - `uv run pytest tests/ -q` -> 42 passed, 14 subtests passed.
  - `.\init.cmd --check-only --skip-tests` -> no failures; warnings only for REST API not running and tests intentionally skipped.
  - `node scripts/harness/validate-harness.mjs` with bundled Node -> weighted total 100/100, all checks passed.
