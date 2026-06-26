# Cross-Session Memory, Progress Tracking & Multi-Agent Handoff

## Overview

This document defines the complete mechanism for:
1. **Cross-session memory persistence** — how the agent remembers context across sessions
2. **Task progress tracking** — how work-in-progress survives session boundaries
3. **Multi-agent task handoff** — how tasks transfer between agents with zero context loss

---

## 1. Cross-Session Memory Persistence

### Architecture

```
┌─────────────────────────────────────────────┐
│              MEMORY.md (Index)              │  ← Bounded: ~200 lines, 25KB max
│  Always loaded into context at session start │
├─────────────────────────────────────────────┤
│  memory/*.md          (Project scope)       │  ← Team-shared, version-controlled
│  .claude/agent-memory/ (Agent scope)        │  ← Per-agent institutional knowledge
│  session-handoff.md   (Session scope)       │  ← Last N sessions for resume
└─────────────────────────────────────────────┘
```

### Memory Lifecycle

```
Session Start
    │
    ├── 1. Read MEMORY.md (index) → discover relevant topic files
    ├── 2. Read session-handoff.md → latest ## Session block
    ├── 3. Read progress.md → active feature + blockers
    │
Session Work
    │
    ├── Agent writes topic file → then updates MEMORY.md index
    ├── progress.md updated after each feature state change
    │
Session End
    │
    ├── 1. Append ## Session block to session-handoff.md
    ├── 2. Update progress.md with final state
    ├── 3. Background: session extraction (if enabled)
    └── 4. Background: orphan cleanup sweep
```

### Memory Type Decision Matrix

| What to save | Memory Type | Example |
|-------------|-------------|---------|
| User's role, expertise, preferences | `user` | "User is a senior backend engineer, prefers terse responses" |
| Corrections or confirmed approaches | `feedback` | "Stop summarizing at end of responses" |
| Project decisions, deadlines, constraints | `project` | "Merge freeze starts 2026-07-01 for Q3 release" |
| External resource pointers | `reference` | "Grafana dashboard at grafana.internal/d/api-latency" |
| Agent performance patterns | Agent memory | "dispatch-log.md records which sub-agents return fastest" |
| Intent classification patterns | Agent memory | "intent-patterns.md records co-occurring intents" |

### Memory Write Protocol (Two-Step)

```
Step 1: Write topic file
  → memory/my-fact.md with frontmatter (name, description, metadata.type)
  
Step 2: Update index
  → MEMORY.md: append "- [Title](my-fact.md) — one-line hook"
  
Crash safety: orphaned topic file is harmless; next sweep cleans it.
```

---

## 2. Task Progress Tracking

### State Machine

```
                  ┌─────────┐
                  │ planned │
                  └────┬────┘
                       │ (agent claims task)
                  ┌────▼────┐
          ┌───────│in_progress│───────┐
          │       └────┬─────┘       │
          │            │              │
    (blocked by     (work            │
     dependency)    complete)         │
          │            │              │
    ┌─────▼────┐  ┌───▼────┐   ┌─────▼────┐
    │ blocked  │  │  done  │   │ abandoned │
    └────┬─────┘  └───┬────┘   └──────────┘
         │             │
    (dependency   (evidence
     resolved)     verified)
         │             │
    ┌────▼─────┐       │
    │in_progress│      │
    └──────────┘       │
                   (archive to
                   completed_order)
```

### feature_list.json — The Source of Truth

```json
{
  "features": {
    "my-feature": {
      "status": "in_progress",       // planned | in_progress | blocked | done | abandoned
      "claimed_by": "agent-name",    // which agent is working on it
      "claimed_at": "2026-06-26",    // when it was claimed
      "progress_pct": 60,            // rough estimate
      "last_evidence": "pytest tests/test_feature.py - 12/12 pass",
      "depends_on": ["other-feature"],
      "blocked_by": ["dependency-feature"],
      "done_criteria": ["Criterion 1", "Criterion 2"]
    }
  }
}
```

### progress.md — The Human-Readable View

Updated by the agent at key moments:
- Feature claimed (status → in_progress)
- Evidence milestone reached (tests passing, code reviewed)
- Blocker encountered (status → blocked, with reason)
- Feature completed (status → done, with evidence)

### Progress Update Checklist

Before marking a feature as `done`:
- [ ] All `done_criteria` checked
- [ ] Tests pass: `uv run pytest tests/ -q`
- [ ] Verification score ≥ 70 (feature gate) or ≥ 85 (release gate)
- [ ] `bash init.sh` clean
- [ ] `node scripts/harness/validate-harness.mjs` no new warnings
- [ ] Evidence recorded in progress.md
- [ ] feature_list.json updated with status: done

---

## 3. Multi-Agent Task Handoff Protocol

### Handoff Package Structure

When one agent needs to transfer work to another, it produces a structured handoff:

```
═══ TASK HANDOFF PACKAGE ═══

📋 TASK IDENTITY
• Task ID: feat-003
• Source Agent: data-analysis-agent
• Target Agent: customer-service-orchestrator
• Handoff Reason: Analytics identified P1 issue requiring orchestrator action
• Priority: P1
• Timestamp: 2026-06-26T14:30:00+08:00

📊 CURRENT STATE
• Feature status: in_progress (60%)
• Completed milestones:
  - [x] Daily analytics report generated
  - [x] Escalation pattern detected: 40% of returns never complete
• In-progress work:
  - Analyzing root cause of stuck returns
• Blocker: None

📁 RELEVANT CONTEXT
• report_path: reports/daily/2026-06-25.md
• Memory files:
  - .claude/agent-memory/data-analysis-agent/escalation-patterns.md
  - .claude/agent-memory/customer-service-orchestrator/dispatch-log.md

✅ VERIFICATION STATE
• Last test run: 2026-06-26 14:00 — 45/45 passing
• Verification score: 92/100
• bash init.sh: clean

⚠️ OPEN RISKS
• Return completion rate dropped 15% this week — investigate
• Low satisfaction scores cluster around after-sales interactions

🔄 RECOMMENDED NEXT ACTION
1. Review analytics report at reports/daily/2026-06-25.md
2. Check return lifecycle — why are 40% stuck at "in_transit"?
3. Consider creating a work order for ops team review
4. Update after-sales-agent prompt with troubleshooting checklist

═══ END HANDOFF ═══
```

### Handoff Trigger Conditions

| Trigger | Handoff Direction | Priority |
|---------|------------------|----------|
| analytics-agent detects escalation pattern | analytics → orchestrator | P1 |
| complaint-agent assesses L3 escalation need | complaint → human-handoff | P1 |
| sub-agent returns `需升级` after retry | any sub-agent → human-handoff | P1 |
| orchestrator detects sub-agent performance degradation | orchestrator → human (administrator) | P2 |
| session handoff (normal end-of-session) | current session → next session | P3 |

### Handoff File Convention

Handoff packages are written to `.claude/handoffs/` with the naming convention:
```
.claude/handoffs/{YYYY-MM-DD}-{from-agent}-to-{to-agent}-{task-id}.md
```

Example: `.claude/handoffs/2026-06-26-analytics-to-orchestrator-return-stuck.md`

### Handoff Integrity Checks

Before accepting a handoff, the receiving agent verifies:
1. Handoff file exists and is readable
2. `📋 TASK IDENTITY` block is complete
3. `📊 CURRENT STATE` includes verifiable evidence (test results, report paths)
4. No circular handoff (agent A → B → A for same task)
5. If handoff path references external files, they exist

---

## 4. Session Resume Protocol

### On Session Start

```
1. Read MEMORY.md (always loaded, bounded)
2. Read progress.md → find Active feature
3. Read session-handoff.md → latest ## Session block
4. If latest session has open risks or in_progress state:
   a. Reconstruct working context from handoff block
   b. Verify referenced files still exist
   c. Check for new git commits since handoff (git log --since=<handoff date>)
5. Run bash init.sh --check-only
6. Resume from "Recommended next action" in handoff
```

### On Session End

```
1. Update progress.md with current state
2. Append ## Session block to session-handoff.md:
   - Active feature + status
   - What was done (bullet list)
   - Files touched (explicit list, not git diff)
   - Current blockers
   - Recommended next action for next session
3. If any background tasks running, note them in session-handoff
4. Run node scripts/harness/validate-harness.mjs --score (for audit trail)
```

### Session Handoff Block Template

```markdown
## Session: YYYY-MM-DD

**Branch**: `main`
**Active feature**: (feature_list.json id)
**Outcome**: (completed | blocked | in_progress)

**What was done**:
- Item 1
- Item 2

**Files touched**:
- path/to/file1 — (created | modified | deleted)
- path/to/file2 — (created | modified | deleted)

**State snapshot**:
- Tests: (X/Y passing)
- Verification score: (X/100)
- Blockers: (list or "none")

**Key decisions made**:
- Decision 1 (rationale)
- Decision 2 (rationale)

**Open risks / follow-ups**:
- Risk 1
- Risk 2

**Recommended next action**:
1. First thing to do
2. Second thing to do
```
