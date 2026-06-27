# Session Handoff — 客服智能体 2.0

> **Purpose**: Make the next session restartable without replaying the entire conversation.
> **When to update**: At the end of every session where work was done. Append a new `## Session` block.
> **When to read**: At the start of every session — check the most recent block to resume.

---

## Session: 2026-06-26 (harness bootstrapping)

**Branch**: `main`
**Active feature**: Harness creator — bootstrapping state tracking and lifecycle artifacts
**Outcome**: Completed. Created `feature_list.json`, `progress.md`, `init.sh`, `session-handoff.md`.

**What was done**:
- Audited harness subsystems (Instructions: strong, State: missing, Verification: partial, Scope: missing, Lifecycle: missing)
- Created `feature_list.json` with 9 completed features + 2 planned, full JSON Schema, dependency graph, and done criteria
- Created `progress.md` with recent completions, blockers, planned next, verification state, and memory index
- Created `init.sh` with 6-stage verification: Python, deps, DB, API health, MCP smoke, tests
- Created `session-handoff.md` (this file) for cross-session context transfer
- Deleted `AGENTS.md` earlier in the session to avoid drift with `CLAUDE.md`

**Files touched this session**:
- `AGENTS.md` — deleted
- `feature_list.json` — created
- `progress.md` — created
- `init.sh` — created
- `session-handoff.md` — created

**State snapshot**:
- All 9 features `done` in feature_list.json
- 2 planned features: production-deployment (P1), load-testing (P2)
- Database: SQLite (`orders.db`), Alembic migrations current
- MCP servers: order-server + customer-service, both configured in `.claude/mcp.json`
- Test suite: `tests/` — 9 test files, pytest runner

**Key decisions made**:
- Harness files live at repo root (not under `.claude/`) for maximum agent visibility
- `feature_list.json` uses JSON Schema for validation, ordered by completion date
- `progress.md` is markdown (not JSON) for human readability in quick scans
- `init.sh` is bash-first with `--check-only` and `--skip-tests` flags for CI friendliness
- `CLAUDE.md` is the sole instruction file; `AGENTS.md` intentionally absent

**Open risks / follow-ups**:
- `init.sh` needs a real run to validate all checks pass on a clean checkout
- `feature_list.json` planned features need grooming into detailed done criteria
- No `PreToolUse` hooks exist for agent dispatch validation (e.g., "never call sub-agent directly")

**Recommended next action**:
1. If starting fresh: run `bash init.sh` and verify all 6 stages pass
2. If continuing development: claim P1 "production-deployment" from feature_list.json
3. If reviewing: run `node scripts/harness/render-assessment-html.mjs` and open `reports/harness-assessment.html`

---

## Session: 2026-06-26 (project reorganization + harness boot hardening)

**Branch**: `main`
**Active feature**: Harness creator — full 7-requirement delivery + project reorganization
**Outcome**: Completed.

**What was done**:
- Reorganized project into professional `src/` layout: 14 core source files → `src/`, 10 utility scripts → `scripts/utils/`, 3 data files → `data/`
- Added `[build-system]` + `[tool.setuptools.packages.find]` to `pyproject.toml`
- Fixed all import paths (sys.path, hardcoded file references, MCP config, alembic env)
- Hardened CLAUDE.md `## Session boot` section: changed from descriptive to imperative (3-step startup + 2-step shutdown)
- Verified: 32/32 tests pass, harness audit 100/100 on all 5 subsystems
- Project root reduced from 35+ files to 15 (config + docs + harness only)

**Files touched this session**:
- `CLAUDE.md` — rewritten "Harness artifacts" → imperative "Session boot" with file index
- `AGENTS.md` — recreated with onboarding context
- `session-handoff.md` — added "Recommended next action" + current session block
- `pyproject.toml` — added build-system + src package discovery
- `alembic.ini` — updated sqlalchemy.url + prepend_sys_path
- `alembic/env.py` — added sys.path for src/
- `.claude/mcp.json` — updated server.py paths to src/
- `.codex/mcp.json` — updated server paths to src/
- `init.sh` — updated orders.db + server paths for src/ + data/
- `src/*.py` — fixed FAQ_PATH, DEFAULT_SQLITE_URL paths
- All 8 `tests/*.py` — fixed faq.json paths + sys.path for src/
- `scripts/utils/*.py` — fixed sys.path for src/
- `data/` — created, moved faq.json, orders.db, test_tokens.json
- `src/` — created with __init__.py, 14 source files

**State snapshot**:
- All 9 features done, 2 planned (P1 production-deployment, P2 load-testing)
- 32/32 tests passing, 0 failures
- Harness audit: 100/100 on all 5 subsystems
- Project folder: professional src/ layout, clean root

**Key decisions made**:
- Used `src/` package layout (PyPA standard) rather than flat layout
- Used `setuptools.build_meta` as build backend (not hatchling/flit)
- Made CLAUDE.md `## Session boot` imperative with explicit numbered steps
- Data files live in `data/` (not `src/data/`) — matches 12-factor app conventions
- Utility scripts live in `scripts/utils/` (not root or tests/) — prevents root clutter

**Open risks / follow-ups**:
- `init.sh` not validated on a completely clean checkout (only tested in current venv)
- `.codex/mcp.json` changed but Codex may need additional configuration for src/ paths
- `docs/standardized-sequences.md` background tasks section references a `cleanup-orphans.mjs` that doesn't exist yet

**Recommended next action**:
1. If continuing development: claim P1 "production-deployment" from feature_list.json
2. If reviewing harness: run `node scripts/harness/render-assessment-html.mjs` and open `reports/harness-assessment.html`
3. If testing: run `uv run pytest tests/ -q` to confirm 32/32 pass

**Branch**: `main`
**Active feature**: (link to feature_list.json id)
**Outcome**: (completed | blocked | in_progress)

**What was done**:
- 

**Files touched this session**:
- 

**State snapshot**:
- 

**Key decisions made**:
- 

**Open risks / follow-ups**:
- 

---

## Session: 2026-06-26 (architecture analysis)

**Branch**: `main`
**Active feature**: None - architecture review only.
**Outcome**: Completed.

**What was done**:
- Reviewed AGENTS.md/session requirements, progress.md, feature_list.json, ADRs, MCP config, agent prompts inventory, and core `src/` modules.
- Analyzed the runtime architecture across orchestrator, MCP tools, REST API, service layer, security, persistence, FAQ retrieval, and analytics.
- Verified the test suite with elevated `uv run pytest tests/ -q`: 32 passed, 14 subtests passed.
- Attempted required `bash init.sh --check-only`, but this Windows environment has no `bash` executable in PATH or common Git Bash install paths.

**Files touched this session**:
- `session-handoff.md` - added this session block only.

**State snapshot**:
- All planned features remain marked done in `feature_list.json`.
- `progress.md` reports no active blockers and planned next work: P1 production deployment hardening, P2 load testing.
- Test health: passing, with warnings for Starlette TestClient deprecation and short dev HMAC secret length.

**Key decisions made**:
- No code changes were made; this was analysis-only.
- Used `codebase-design` vocabulary to frame module interfaces, seams, adapters, leverage, and locality.

**Open risks / follow-ups**:
- `init.sh --check-only` still cannot run until Bash is available on the host.
- Existing Chinese text renders as mojibake in PowerShell output, suggesting terminal/codepage display issues even though file paths and execution work.

---

## Session: 2026-06-26 (risk hardening)

**Branch**: `main`
**Active feature**: Risk hardening after architecture review.
**Outcome**: Completed.

**What was done**:
- Added cross-platform init implementation: `scripts/harness/init_check.py`, plus `init.cmd`, `init.ps1`, and a simplified `init.sh` wrapper.
- Rewrote key governance docs (`AGENTS.md`, `CLAUDE.md`) as readable ASCII instructions with Windows/POSIX startup paths.
- Removed `IDENTITY_VERIFICATION` from `order-server` MCP config and made `src/server.py` scrub that env var at startup.
- Replaced short dev/test HMAC secrets with >=32-byte values and regenerated MCP dev JWTs.
- Added risk regression tests in `tests/test_harness_risk_controls.py`.
- Cleaned pytest warning output with a precise Starlette TestClient deprecation filter.
- Added `.gitattributes` and adjusted `validate-harness.mjs` to accept the Windows `init.cmd` verification entry point.

**Files touched this session**:
- `.gitattributes`, `.claude/mcp.json`, `.claude/settings.json`, `AGENTS.md`, `CLAUDE.md`, `init.cmd`, `init.ps1`, `init.sh`
- `scripts/harness/init_check.py`, `scripts/harness/validate-harness.mjs`
- `src/security.py`, `src/server.py`
- `tests/test_harness_risk_controls.py`, plus targeted test imports/secrets in FastAPI/security tests
- `pyproject.toml`, `progress.md`, `session-handoff.md`

**State snapshot**:
- `uv run pytest tests/ -q`: 37 passed, 14 subtests passed, 0 warnings.
- Harness audit with bundled Node: 100/100, all checks passed.
- `./init.cmd --check-only --skip-tests`: no failures; warnings only for REST API not running and skipped tests.
- Full `./init.cmd` ran successfully once after adding the entrypoint; a later rerun was blocked by platform usage limits, not project failure.

**Key decisions made**:
- Treat `customer-service` MCP `handle_customer_message` as the only customer-facing MCP path.
- Keep `order-server` read-only and unable to forward scoped customer verification tokens.
- Prefer ASCII/no-BOM governance and harness files to avoid Windows terminal/codepage corruption.

**Open risks / follow-ups**:
- REST API was not running during check-only validation; start `uvicorn order_api:app --reload --port 8000` when live API probes are required.
- Broader product prompt/FAQ Chinese text still contains mojibake in parts of the repository; this session fixed governance/runtime-risk surfaces only.
---

## Session: 2026-06-27 (agent latency hardening implementation)

**Branch**: `main`
**Active feature**: Agent response speed hardening based on prior latency diagnosis.
**Outcome**: Implemented; core regression and performance checks passed; full-suite verification blocked by Windows permission issues in temporary directories.

**What was done**:
- Added Fast Agent Workflow rules to `AGENTS.md` and `CLAUDE.md`: prefer Scoop `rg`, avoid unrestricted recursive PowerShell search, use UTF-8/Unicode fixtures for Chinese, and reuse MCP/same Python process for diagnostics.
- Made `src/server_customer.py` lazy-load FAQ retriever/categories and `analytics_service` for report generation only.
- Made `src/orchestrator_runtime.py` lazy-load FAQ retriever only when `search_faq` is called, and lazy-load `analytics_service` only when recording usage events.
- Added regression coverage in `tests/test_harness_risk_controls.py` for workflow rules and lazy cold-start behavior.

**Verification evidence**:
- `.\init.cmd --check-only --skip-tests`: usable; warnings only for REST API not running and skipped tests.
- `$env:Path="$env:USERPROFILE\scoop\shims;$env:Path"; rg --version`: ripgrep 15.1.0.
- `.\.venv\Scripts\python.exe -m pytest tests\test_harness_risk_controls.py -q -p no:cacheprovider`: 8 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_harness_risk_controls.py tests\test_rag_faq.py tests\test_rag_customer_scenarios.py -q -p no:cacheprovider`: 14 passed, 14 subtests passed.
- Performance probes: `server_customer` import leaves `analytics_loaded=false` and `faq_loaded=false`; non-FAQ complaint path leaves `faq_factory_calls=0` before expected identity-verification failure.

**Verification caveats**:
- `uv run pytest ...` failed before tests because uv could not write/read under user AppData cache/temp paths in this sandbox.
- Full `.venv` pytest run reached 39 passed, 14 subtests passed, then failed in `DailyAnalyticsTest.test_cli_writes_markdown_report` due temporary-directory permission/cleanup failures unrelated to this change.
- Two directories created during the failed temp-dir verification could not be removed because Windows denied access: `.tmp-pytest` and `.tmp-pytest-analytics`.
- There is an unrelated untracked root `order_api.py` in the working tree; this session did not modify it.

**Open follow-ups**:
- Clear the locked `.tmp-pytest*` directories after the OS releases them or from an elevated shell.
- Investigate why pytest subprocess temp directories under this sandbox get ACL-denied during daily analytics CLI tests.

---

## Session: 2026-06-27 (agent reliability hardening implementation)

**Branch**: `main`
**Active feature**: First batch of agent improvement plan: partial results, multi-write idempotency, and conversation context.
**Outcome**: Implemented; focused regression tests passed; full-suite verification still blocked by Windows temp/uv permission issues.

**What was done**:
- Changed `src/orchestrator_mcp_tool.py` so only 401/403 `HTTPException`s become `denied`; other business/runtime errors become `failed` and no longer claim that no business writes occurred.
- Changed `src/orchestrator_runtime.py` order inquiry handling so missing shipment records return a partial business result after successful order lookup instead of aborting a multi-intent request.
- Added per-operation idempotency key derivation for orchestrator fan-out writes, so one caller key can safely cover survey, return, low-score ticket, and complaint ticket writes.
- Added bounded in-process conversation state keyed by `conversation_id`, storing only recent `customer_id` and `order_id` for follow-up turns.
- Added E2E coverage for partial-after-writes MCP behavior, idempotent retry of multi-intent writes, and follow-up return requests that reuse prior conversation order context.

**Verification evidence**:
- `.\.venv\Scripts\python.exe -m pytest tests\test_orchestrator_e2e.py -q -p no:cacheprovider`: 7 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_harness_risk_controls.py tests\test_rag_faq.py tests\test_rag_customer_scenarios.py tests\test_security_controls.py -q -p no:cacheprovider`: 23 passed, 14 subtests passed.
- `.\init.cmd --check-only --skip-tests`: no failures; warnings only for REST API not running and skipped tests.
- `$env:Path="$env:USERPROFILE\scoop\shims;$env:Path"; rg --version`: ripgrep 15.1.0.

**Verification caveats**:
- Full `.venv` pytest currently reports 41 passed, 14 subtests passed, then fails in `DailyAnalyticsTest.test_cli_writes_markdown_report` because Windows denies access to the generated temp report directory during subprocess/cleanup.
- `uv run pytest ...` is blocked before test execution by Windows permission errors in uv cache/build temp directories, including after setting `UV_CACHE_DIR` inside the workspace.
- Locked temp directories remain visible to `git status` warnings: `.pytest_cache`, `.tmp-pytest`, `.tmp-pytest-analytics`, and `.tmp-test-run`.

**Open follow-ups**:
- Clear locked temp directories after the OS releases them or from an elevated shell.
- Investigate the daily analytics CLI test's subprocess temp-directory ACL behavior separately from orchestrator reliability work.

---

## Session: 2026-06-27 (live complaint routing check)

**Branch**: `main`
**Active feature**: None; customer-message handling verification only.
**Outcome**: Routed a live Chinese complaint/manager-escalation message through the customer-facing Orchestrator entry point; no code changes.

**What was done**:
- Ran `.\init.cmd --check-only --skip-tests`; project usable with expected warnings for REST API not running and skipped tests.
- Read `progress.md` and `feature_list.json`; no active blockers or in-progress features.
- Invoked `server_customer.handle_customer_message` with the customer message via the Orchestrator role.

**Verification evidence**:
- Orchestrator MCP path returned `status=denied` with `missing_identity_verification`, so no complaint ticket or human handoff write was created.

**Open follow-ups**:
- To complete a real manager/human handoff workflow for this complaint, provide or obtain a scoped identity verification token and customer/order context.

---

## Session: 2026-06-27 (cold-start response optimization)

**Branch**: `main`
**Active feature**: Customer-message cold-start latency optimization.
**Outcome**: Implemented; full regression and harness verification passed.

**What was done**:
- Added `src/api_dependencies.py` for FastAPI-only dependency helpers and removed FastAPI imports from `security.py`.
- Switched core runtime/service exception imports to Starlette while keeping `order_api.py` as the FastAPI adapter.
- Updated tests to assert Starlette `HTTPException`, matching the core-layer exception type.
- Added Fast Agent Workflow guidance to use `orchestrator_mcp_tool.handle_customer_message_tool` for one-off diagnostics instead of importing full `server_customer.py`.

**Verification evidence**:
- `python -X importtime -c "import orchestrator_mcp_tool"`: filtered output showed no `fastapi`; `orchestrator_mcp_tool` cumulative import about 0.82s.
- `Measure-Command { python -c "import orchestrator_mcp_tool" }`: 1.02s; `Measure-Command { python -c "import server_customer" }`: 3.64s.
- Lightweight complaint probe: 0.0302s handler time, `status=denied`, `error=missing_identity_verification`.
- Valid verification complaint probe: `status=needs-human`, `emotional_level=L2`, created 1 ticket.
- `.\.venv\Scripts\python.exe -m pytest tests\test_orchestrator_e2e.py -q -p no:cacheprovider`: 7 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_security_controls.py tests\test_harness_risk_controls.py -q -p no:cacheprovider`: 17 passed.
- `uv run pytest tests/ -q`: 42 passed, 14 subtests passed.
- `.\init.cmd --check-only --skip-tests`: usable; warnings only for REST API not running and skipped tests.
- Bundled Node harness validator: weighted total 100/100, all checks passed.

**Open follow-ups**:
- Full MCP server import still costs about 3.6s because FastMCP itself pulls a heavy dependency chain; optimize separately only if MCP process startup, rather than one-off diagnostics, remains user-visible.

---

## Session: 2026-06-27 (production hardening implementation)

**Branch**: `main`
**Active feature**: Production-grade customer-service hardening.
**Outcome**: Implemented major P0/P1 hardening slices and verified full local test gate.

**What was done**:
- Removed hardcoded MCP credentials and added `.env.example`.
- Added production runtime config validation, readiness, metrics, JSON body v2 write endpoints, Dockerfile, docker-compose MySQL stack, and production hardening docs.
- Added dispatcher module interface with evidence/fallback metadata and a hybrid fallback seam.
- Added durable conversation state table/migration and handoff packages for human escalation.
- Reworked Alembic `0001_initial_schema` into explicit operations and added `0003_conversation_states`.
- Added in-process sequence guards for ticket/return/survey numbers under concurrent local writes.
- Stabilized daily analytics CLI output tests under Windows workspace paths.
- Hardened `scripts/harness/init_check.py` to use UTF-8 subprocess decoding and `.venv` for migration/test stages.

**Verification evidence**:
- `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider`: 48 passed, 14 subtests passed.
- `.\init.cmd`: all stages pass; warning only for REST API localhost:8000 not running.
- Bundled Node `scripts/harness/validate-harness.mjs`: weighted total 100/100, all checks passed.

**Open follow-ups**:
- Raw `uv run pytest tests/ -q` remains blocked by Windows/OneDrive uv cache/editable-build ACL errors in this environment; init now avoids that path by using `.venv`.
- Multi-process numbering should eventually move from in-process sequence guards to a DB-native sequence/counter adapter before horizontal API scaling.
- External OTP, OIDC/JWKS, analytics webhook/email, and true LLM/RAG dispatcher providers still need real credentials/provider adapters for production rollout.
