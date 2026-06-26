# Progress — 客服智能体 2.0

> Last updated: 2026-06-26
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