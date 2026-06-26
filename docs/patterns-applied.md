# 7 Reference Patterns — Applied to 客服智能体 2.0

> Each pattern from the harness-creator skill is mapped to a concrete implementation in this project. "Status" indicates whether the pattern is fully implemented, partially implemented, or planned.

---

## 1. Memory Persistence Pattern

**Source**: `references/memory-persistence-pattern.md`
**Status**: ✅ Partially implemented — needs enhancement

### How it maps to this project

| Pattern Rule | Project Implementation | Status |
|-------------|----------------------|--------|
| Separate layers by scope | Project: `memory/MEMORY.md` + topic files. Agents: `.claude/agent-memory/<agent-name>/` per-agent topic files | ✅ |
| Two-step save invariant | Orchestrator system prompt instructs: write topic file → update MEMORY.md index | ✅ |
| Local overrides win | `CLAUDE.md` (project) > agent memory (runtime). Settings: `settings.local.json` > `settings.json` | ✅ |
| Bounded index | `MEMORY.md` limited to ~200 lines with one-line hooks per entry | ✅ |
| Four-type taxonomy | `user`, `feedback`, `project`, `reference` — defined in each agent's system prompt | ✅ |

### Gaps to address

1. **Orphan cleanup**: No periodic sweep for orphaned topic files (gotcha #15). Add a cleanup script.
2. **Team memory**: Currently project-scoped only. To enable team memory, ensure auto-memory is enabled (gotcha #14).
3. **Extraction race window**: Background extraction fires at session end; user can start next turn before it completes (gotcha #3). Consider coalescing extraction requests.

### Implementation path

```bash
# Add to init.sh: memory health check
find .claude/agent-memory -name "*.md" ! -name "MEMORY.md" | while read f; do
  slug=$(basename "$f" .md)
  if ! grep -q "$slug" .claude/agent-memory/*/MEMORY.md 2>/dev/null; then
    echo "  ⚠ Orphaned memory: $f"
  fi
done
```

---

## 2. Skill Runtime Pattern

**Source**: `references/skill-runtime-pattern.md`
**Status**: ✅ Implemented

### How it maps to this project

| Pattern Rule | Project Implementation | Status |
|-------------|----------------------|--------|
| Progressive disclosure | SKILL.md (entry) → references/ (deep material) → templates/ (copyable artifacts) | ✅ |
| SKILL.md frontmatter | `.claude/skills/minimal-order-mcp/SKILL.md` triggers when user mentions "订单 MCP" | ✅ |
| References loaded on demand | `.agents/skills/harness-creator/references/` — 7 pattern docs, loaded only when relevant | ✅ |
| Templates safe to copy | `init.sh`, `session-handoff.md` templates are project-agnostic | ✅ |
| Scripts as optional helpers | `scripts/harness/` — Node.js scripts are helpers, not hidden behavior | ✅ |

### Skills inventory

| Skill | Location | Trigger |
|-------|----------|---------|
| harness-creator | `.claude/skills/harness-creator` (symlink → `.agents/skills/harness-creator`) | `/harness-creator` |
| minimal-order-mcp | `.claude/skills/minimal-order-mcp` | "订单 MCP", "expose order data" |
| setup-matt-pocock-skills | `.agents/skills/setup-matt-pocock-skills` | `/setup-matt-pocock-skills` |
| diagnosing-bugs | `.agents/skills/diagnosing-bugs` | `/diagnose` |
| tdd | `.agents/skills/tdd` | `/tdd` |
| codebase-design | `.agents/skills/codebase-design` | Design/refactor requests |

---

## 3. Context Engineering Pattern

**Source**: `references/context-engineering-pattern.md`
**Status**: ✅ Implemented

### How it maps to this project

| Pattern Rule | Project Implementation | Status |
|-------------|----------------------|--------|
| **SELECT** (JIT loading) | Agent system prompts loaded on demand via `.claude/agents/*.md`. ADRs read when relevant. FAQ loaded at query time. | ✅ |
| **WRITE** (agent writes back) | Agents write to `.claude/agent-memory/`. Progress tracked in `progress.md`. session-handoff appended at session end. | ✅ |
| **COMPRESS** (mid-session) | Claude Code auto-compacts when context ≥ 80%. `progress.md` tracks compaction checkpoints. | ✅ |
| **ISOLATE** (delegation) | Sub-agents start fresh — Orchestrator sends only `【客户上下文】+【任务】`, not full history. | ✅ |
| Three-tier loading | Tier 1: feature_list.json + MEMORY.md (always). Tier 2: CLAUDE.md (on activate). Tier 3: ADRs + skill references (on demand). | ✅ |
| Manual cache invalidation | `progress.md` updated explicitly at each feature completion. `session-handoff.md` blocks are append-only. | ✅ |

### Context budget for this project

| Category | Budget | Status |
|----------|--------|--------|
| System prompt (CLAUDE.md) | ~2,500 chars | Within budget |
| feature_list.json | ~4,000 chars | Within budget |
| Memory index (MEMORY.md) | Variable, ~200 lines cap | Monitor |
| Agent system prompts | 9 agents × ~8KB avg = ~72KB total | Loaded on demand only |
| Session handoff blocks | ~2KB per session | Trim to last 3 sessions |

---

## 4. Tool Registry & Safety Pattern

**Source**: `references/tool-registry-pattern.md`
**Status**: ✅ Implemented

### How it maps to this project

| Pattern Rule | Project Implementation | Status |
|-------------|----------------------|--------|
| Fail-closed defaults | Tools in `settings.local.json` explicitly allowlisted. All others default to ask/deny. | ✅ |
| Per-call concurrency | Bash safety hook checks each command for destructive patterns before execution | ✅ |
| Permission pipeline | `settings.local.json` permissions → project `.claude/settings.json` hooks → session grants | ✅ |
| Bypass-immune paths | PreToolUse hook blocks writes to `/etc/*`, `/Windows/*`, `~/.ssh/*`, `~/.gnupg/*` | ✅ |
| Protected commands | `rm -rf`, `git push --force`, `git reset --hard`, `chmod 777`, `curl | sh` — all blocked | ✅ |
| Credential leak detection | PostToolUse hook flags `.env`, `credential`, `private key`, `.pem`, `.key`, `token` files | ✅ |
| User prompt scanning | UserPromptSubmit hook detects API keys/secrets/passwords in prompts | ✅ |

### Permission tier mapping (ADR-0004 compliant)

| Agent Tier | Allowed Tools | Blocked Tools |
|-----------|---------------|---------------|
| L0 (read-only) | Read, Grep, Glob, WebFetch, WebSearch, MCP read-only tools | Write, Edit, Bash, MCP write tools |
| L1 (light ops) | L0 + Write, Edit, MCP create/update tools | Destructive Bash, direct DB access |
| L2 (conversation only) | Read, Write, Edit (for records only) | All MCP tools, Bash, WebFetch, WebSearch |

---

## 5. Multi-Agent Coordination Pattern

**Source**: `references/multi-agent-pattern.md`
**Status**: ✅ Implemented

### How it maps to this project

| Pattern Rule | Project Implementation | Status |
|-------------|----------------------|--------|
| **Coordinator pattern** | Orchestrator synthesizes Dispatcher output before dispatching to sub-agents. Never delegates understanding. | ✅ |
| **Zero context inheritance** | Sub-agents start fresh. Only `【客户上下文】+【任务】` passed via ADR-0002 protocol. | ✅ |
| **Fork restricted to single-level** | Sub-agents cannot spawn other sub-agents. All routing goes back through Orchestrator. | ✅ |
| **Tool filtering per worker** | Each sub-agent has a specific tool set matching its permission tier. L0 agents have no write tools. L2 agents have only Read. | ✅ |
| **Fire-and-forget registration** | Dependency test determines serial vs parallel dispatch. Parallel sub-agents launched simultaneously. | ✅ |
| **Self-contained prompts** | Every sub-agent dispatch includes explicit `【任务】` block with specific instructions, not "based on your findings." | ✅ |

### Dispatch topology

```
Customer Message
      │
      ▼
Orchestrator (synthesizes, dispatches)
      │
      ├── [parallel group 1] ──────────────────
      │   ├── order-inquiry-agent (L0, fresh ctx)
      │   └── consultation-agent (L0, fresh ctx)
      │
      ├── [sequential: depends on group 1] ────
      │   └── complaint-agent (L2, fresh ctx)
      │
      └── [parallel group 2] ──────────────────
          ├── after-sales-agent (L1, fresh ctx)
          └── work-order-agent (L1, fresh ctx)
```

### Anti-patterns avoided

- ✗ "Based on your findings, fix the auth system" → Orchestrator must synthesize first
- ✗ Sub-agent calling another sub-agent → all routing through Orchestrator
- ✗ Full conversation history passed to sub-agent → only `【客户上下文】` block

---

## 6. Lifecycle & Bootstrap Pattern

**Source**: `references/lifecycle-bootstrap-pattern.md`
**Status**: ✅ Implemented

### How it maps to this project

| Pattern Rule | Project Implementation | Status |
|-------------|----------------------|--------|
| **Hook trust all-or-nothing** | 6 hooks in `.claude/settings.json` — if any unsafe pattern detected, entire operation blocked | ✅ |
| **Typed prefixed IDs** | Conversation IDs (`conv-*`), ticket numbers (`TKT-*`), return IDs (`RMA-*`), survey IDs (`SUR-*`) | ✅ |
| **Disk-backed output** | Usage analytics written to `reports/daily/`. Agent memory persisted to `.claude/agent-memory/`. | ✅ |
| **Two-phase eviction** | Analytics reports cleaned eagerly. Session memory cleaned lazily after parent notified. | ⚠️ Partial |
| **Dependency-ordered bootstrap** | Stage 1: venv check → Stage 2: dependencies → Stage 3: DB migration → Stage 4: API health → Stage 5: MCP smoke → Stage 6: tests | ✅ |
| **Memoized stages** | `init.sh --check-only` skips install/migration if already current | ✅ |

### Bootstrap sequence (implemented in `init.sh`)

```
Stage 1: Python version        → gate: ≥ 3.10
Stage 2: Dependencies (uv)     → gate: uv sync success
Stage 3: Database + seed       → gate: orders.db exists + alembic current
Stage 4: API health            → gate: localhost:8000 responds 200
Stage 5: MCP server smoke      → gate: .claude/mcp.json valid + servers present
Stage 6: Test suite            → gate: pytest passes
```

### Hook inventory (`.claude/settings.json`)

| Hook Type | Matcher | Purpose |
|-----------|---------|---------|
| PreToolUse | Bash | Block destructive commands (rm -rf, push --force, etc.) |
| PreToolUse | Write/Edit | Warn on sensitive paths (/etc, /Windows, ~/.ssh) |
| PreToolUse | WebFetch | Block internal network requests (localhost, 192.168.x) |
| PostToolUse | Write/Edit | Flag sensitive file writes (.env, credentials, keys) |
| UserPromptSubmit | (all) | Detect credentials in user prompts |

---

## 7. Gotchas — 15 Failure Modes

**Source**: `references/gotchas.md`
**Status**: Documented with mitigations

### Gotcha-to-Fix mapping for this project

| # | Gotcha | Impact on This Project | Mitigation |
|---|--------|----------------------|------------|
| 1 | Memory index caps fire silently | MEMORY.md may truncate entries silently | Enforce one-line hooks in agent prompts |
| 2 | Priority ordering is counterintuitive | `settings.local.json` can override project hooks | Document override chain in CLAUDE.md |
| 3 | Extraction timing creates race window | Agent memory write vs next user turn | Use idempotency keys on writes |
| 4 | Derivable content in memory | Agent saves code patterns already in docs | Type taxonomy excludes derivable content |
| 5 | Concurrent classification is per-call | Parallel sub-agent dispatch could conflict | Dependency test prevents conflicting parallel dispatch |
| 6 | Permission evaluation has side effects | Permission denials tracked, modes auto-transition | Don't cache permission results |
| 7 | Most async work skips "pending" | Sub-agents start directly as "running" | Progress tracking uses state: in_progress, not pending |
| 8 | Fork children must not fork | Sub-agents cannot spawn other sub-agents | ADR-0001 enforces single-level delegation |
| 9 | Context builders memoized but not invalidated | `progress.md` cache may go stale | Explicit updates at each feature completion |
| 10 | Hook trust is all-or-nothing | One bad hook disables all extensions | All hooks reviewed and committed to version control |
| 11 | Eviction requires notification | Session handoff before memory GC | Two-phase: write handoff → notify → GC old sessions |
| 12 | Skill listing budgets are tight | Skill descriptions truncated if too long | Keep descriptions under 150 chars |
| 13 | Default tool permission is "allow" | `settings.local.json` provides explicit allowlist | All other tools default to ask |
| 14 | Team memory requires auto-memory | Team-shared memory disabled if auto-memory off | Verify auto-memory gate before enabling team memory |
| 15 | Orphaned topic files accumulate | `.claude/agent-memory/**/*.md` can accumulate | Periodic sweep in init.sh (planned) |

### Gotcha severity for this project

| Severity | Count | Action |
|----------|-------|--------|
| 🔴 High (will cause bugs) | 3 (#5, #8, #13) | Already mitigated by design (ADR-0001, ADR-0004, settings) |
| 🟡 Medium (may cause issues) | 7 (#1, #2, #3, #6, #9, #11, #14) | Documented, monitoring needed |
| 🟢 Low (cosmetic/slow-burn) | 5 (#4, #7, #10, #12, #15) | Addressed by conventions, add cleanup later |
