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
