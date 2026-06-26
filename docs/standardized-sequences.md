# Standardized Sequences — 客服智能体 2.0

## 1. Project Initialization Sequence

### Fresh checkout (new developer / new machine)

```bash
# Step 1: Clone
git clone git@github.com:K-boy-666/Customer-Service-Agent.git
cd Customer-Service-Agent

# Step 2: Environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install uv
uv sync
uv sync --extra rag  # optional: RAG FAQ embedding support

# Step 3: Database
uv run alembic upgrade head
uv run python seed_data.py

# Step 4: Verification
bash init.sh  # full 6-stage verification

# Step 5: Start API (if running without MCP auto-launch)
uvicorn order_api:app --reload --port 8000 &

# Step 6: Ready
echo "✅ 客服智能体 2.0 is ready."
echo "   Test with: uv run pytest tests/ -q"
echo "   MCP servers auto-launch via .claude/mcp.json"
```

### CI / automated environment

```bash
# Minimal CI init (no API server needed for tests)
bash init.sh --check-only --skip-tests
uv run alembic upgrade head
uv run pytest tests/ -q --tb=short
node scripts/harness/validate-harness.mjs --score
```

### What init.sh covers

```
Stage 1: Python 3.10+ check     → gate: version ≥ 3.10
Stage 2: Dependencies (uv)      → gate: uv sync success, .venv exists
Stage 3: Database + migrations  → gate: orders.db exists, alembic current
Stage 4: API health             → gate: localhost:8000 GET /api/orders/stats returns 200
Stage 5: MCP server smoke       → gate: .claude/mcp.json valid, server.py + server_customer.py present
Stage 6: Test suite             → gate: pytest returns 0
```

---

## 2. Background Tasks

### Architecture

```
┌──────────────────────────────────────────┐
│           Background Task Registry        │
│                                           │
│  Task ID prefix convention:              │
│    extraction-*    Session memory        │
│    benchmark-*     Performance tests     │
│    report-*        Daily/weekly reports  │
│    cleanup-*       Maintenance sweeps    │
│    migration-*     DB schema changes     │
└──────────────────────────────────────────┘
```

### Registered Background Tasks

| Task ID | Schedule | Command | Output | Lifecycle |
|---------|----------|---------|--------|-----------|
| `report-daily-001` | Daily 00:10 Asia/Shanghai | `python scripts/generate_daily_usage_report.py` | `reports/daily/YYYY-MM-DD.md` | running → completed (disk output) → evicted after parent reads |
| `cleanup-memory-orphans` | Weekly Sun 03:00 | `find .claude/agent-memory -name "*.md" ! -name "MEMORY.md"` sweep | console log | running → completed → evicted |
| `extraction-session` | On session end | Background session transcript analysis | `.claude/agent-memory/*/session-insights.md` | running → completed → evicted after next session start |
| `benchmark-scheduled` | On demand / CI | `node scripts/harness/run-benchmark.mjs --ci` | `reports/benchmark.json` | running → completed → evicted |

### Task Lifecycle State Machine

```
register → running ──────→ completed ──→ notified ──→ [GC]
              │               │
              ├──────────────→ failed ────→ notified ──→ [GC]
              │
              └──────────────→ killed ────→ notified ──→ [GC]

Invariants:
  - Terminal states (completed/failed/killed) clean disk output eagerly
  - In-memory records cleaned lazily after parent notified
  - "pending" state skipped — tasks register directly as "running"
  - Two-phase eviction: disk cleanup (eager) → memory cleanup (lazy post-notify)
```

### Background Task Bootstrap (in settings.json or hook)

```json
{
  "backgroundTasks": {
    "report-daily-001": {
      "schedule": "0 10 0 * * *",
      "timezone": "Asia/Shanghai",
      "command": "python scripts/generate_daily_usage_report.py",
      "outputDir": "reports/daily",
      "onFailure": "log-and-continue"
    },
    "cleanup-memory-orphans": {
      "schedule": "0 0 3 * * 0",
      "command": "node scripts/harness/cleanup-orphans.mjs",
      "onFailure": "log-and-continue"
    }
  }
}
```

### Long-Running Task Monitoring

```bash
# Check background task status
node scripts/harness/task-status.mjs

# Output:
#   report-daily-001      completed   2026-06-26 00:10:03   reports/daily/2026-06-25.md
#   cleanup-orphans       running     started 2026-06-23 03:00
#   extraction-session    completed   2026-06-26 15:45:22   .claude/agent-memory/.../insights.md
```

---

## 3. Session Boot Sequence

### Standard session start

```
┌─────────────────────────────────────────────┐
│ 1. ENVIRONMENT CHECK                        │
│    bash init.sh --check-only --skip-tests   │
│    → Verify: Python, deps, DB, MCP config   │
│    → ~5 seconds                             │
├─────────────────────────────────────────────┤
│ 2. CONTEXT LOAD                             │
│    Read: progress.md (active feature)       │
│    Read: session-handoff.md (latest block)  │
│    Read: feature_list.json (dependencies)   │
│    Read: MEMORY.md (always in context)      │
│    → ~2 seconds                             │
├─────────────────────────────────────────────┤
│ 3. STATE RECONSTRUCTION                     │
│    - Identify active feature from progress  │
│    - Check for open blockers                │
│    - Verify referenced files exist          │
│    - Check git log for new commits since    │
│      last handoff                           │
│    → ~3 seconds                             │
├─────────────────────────────────────────────┤
│ 4. WORK RESUMPTION                          │
│    - If active feature: resume from          │
│      "Recommended next action" in handoff    │
│    - If no active feature: review planned    │
│      features, claim next P1 task           │
│    - If blockers: address blocker first     │
│    → variable                               │
├─────────────────────────────────────────────┤
│ 5. VERIFICATION GATE                        │
│    - Quick path: bash init.sh (full)        │
│    - CI path: node scripts/harness/         │
│      validate-harness.mjs --score           │
│    → ~30 seconds (quick) / ~5 min (full)    │
└─────────────────────────────────────────────┘
```

### Session boot checklist

```markdown
## Session Boot — YYYY-MM-DD

### Gate checks
- [ ] `bash init.sh --check-only --skip-tests` passes
- [ ] `progress.md` read — active feature identified
- [ ] `session-handoff.md` read — last session state loaded
- [ ] `feature_list.json` read — dependency graph understood
- [ ] No new git commits since last handoff (or noted if present)

### State reconstruction
- [ ] Active feature: (name) — status: (planned|in_progress|blocked)
- [ ] Blocker: (yes/no — if yes, what?)
- [ ] Last session outcome: (completed|blocked|in_progress)
- [ ] Recommended next action: (from handoff)

### Decision
- [ ] Resume active feature
- [ ] OR claim new feature from planned list
- [ ] OR address blocker

### Start work
- [ ] If resuming: pick up from handoff "Recommended next action"
- [ ] If new: update feature_list.json (status → in_progress)
- [ ] If blocked: report blocker status, check if dependency resolved
```

### Session end checklist

```markdown
## Session End — YYYY-MM-DD

### State capture
- [ ] progress.md updated with current state
- [ ] feature_list.json reflects feature status changes
- [ ] All test evidence recorded

### Handoff
- [ ] Session block appended to session-handoff.md
- [ ] Block includes: active feature, what was done, files touched,
      state snapshot, key decisions, open risks, recommended next action

### Verification
- [ ] `bash init.sh` clean (or documented warnings)
- [ ] `node scripts/harness/validate-harness.mjs --score` captured
- [ ] Any background tasks noted in handoff

### Cleanup
- [ ] Stale branches considered for removal
- [ ] Temporary files cleaned
- [ ] Agent memory updated (if session had learnings)
```

---

## 4. Agent Dispatch Sequence (Runtime)

### Single-intent dispatch

```
1. Orchestrator receives customer message
2. Emotional scan (L1/L2/L3 keyword check)
3. If L2/L3: route to complaint/human-handoff immediately
4. If L1:
   a. Call Dispatcher: send raw message, receive structured intent
   b. If confidence ≥ 80%: dispatch to corresponding sub-agent
   c. If confidence < 60%: ask clarifying question
   d. If multi-intent: apply dependency test, then parallel or sequential
5. Sub-agent receives 【客户上下文】+【任务】
6. Sub-agent calls MCP tools, constructs 【客户回复】
7. Orchestrator collects all 【客户回复】 blocks
8. Orchestrator synthesizes into single coherent response
9. Orchestrator sends to customer
10. Loop to step 1 for next customer message
```

### Multi-intent parallel dispatch

```
Customer: "我的订单没收到，而且产品质量有问题，我要退款和投诉！"

Orchestrator:
  │
  ├── Dispatcher analysis:
  │   - 查订单 (85%) → order-inquiry-agent
  │   - 售后 (80%)   → after-sales-agent
  │   - 投诉 (75%)   → complaint-agent
  │
  ├── Dependency test:
  │   - 查订单 results needed by 售后 (order status needed for return)
  │   - 投诉 is independent
  │
  ├── Dispatch plan:
  │   Phase 1 (parallel):
  │     ├── order-inquiry-agent  ← 查订单状态
  │     └── complaint-agent      ← 安抚 + 记录投诉
  │   Phase 2 (depends on Phase 1):
  │     └── after-sales-agent    ← 使用订单信息创建退货
  │
  └── Integration:
      "您订单 ORD-xxx 目前... [order-inquiry result]
       非常抱歉给您带来不愉快的体验 [complaint acknowledgment]
       已为您创建退货申请 RMA-xxx [after-sales result]"
```

---

## 5. Emergency Sequences

### Security incident

```
1. Detected by: PreToolUse hook (destructive command) OR UserPromptSubmit hook (credential leak)
2. Action: Block immediately, log incident
3. Notify: human-handoff-agent (L3 escalation)
4. Record: security incident in .claude/incidents/YYYY-MM-DD-<type>.md
5. Follow-up: review hooks, tighten allowlist
```

### Sub-agent failure loop

```
1. Sub-agent returns 【处理结果】: 无法处理
2. Orchestrator retries ONCE with simplified context
3. If still fails: escalate to human-handoff-agent
4. human-handoff prepares package → marks for human review
5. Session handoff records the failure for next session
```

### Database migration failure

```
1. alembic upgrade head fails
2. init.sh Stage 3 blocks
3. Check: alembic history → is this a known migration gap?
4. Fix: either apply missing migration or mark as current
5. Re-run: bash init.sh
```

---

## 6. Quick Reference Card

```
╔═══════════════════════════════════════════════╗
║  客服智能体 2.0 — Quick Reference            ║
╠═══════════════════════════════════════════════╣
║                                               ║
║  Start session:  bash init.sh --check-only    ║
║  Full verify:    bash init.sh                 ║
║  Run tests:      uv run pytest tests/ -q      ║
║  Validate:   node scripts/harness/validate    ║
║                   -harness.mjs                ║
║  Benchmark:   node scripts/harness/run        ║
║                   -benchmark.mjs              ║
║  HTML report: node scripts/harness/render     ║
║                   -assessment-html.mjs        ║
║  Start API:   uvicorn order_api:app           ║
║                   --reload --port 8000        ║
║                                               ║
║  Active feature:  cat progress.md             ║
║  Feature state:   cat feature_list.json       ║
║  Resume from:     cat session-handoff.md      ║
║  Architecture:    cat CONTEXT.md              ║
║  Decisions:       ls docs/adr/                ║
║                                               ║
║  Entry point:  customer-service-orchestrator  ║
║  Protocol:     ADR-0002 【客户上下文】格式    ║
║  Permissions:  ADR-0004 L0/L1/L2 tiers       ║
║  Escalation:   ADR-0003 L1→L2→L3 ladder      ║
║                                               ║
╚═══════════════════════════════════════════════╝
```
