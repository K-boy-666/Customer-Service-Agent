# Production Verification Workflow

## Overview

Five dimensions, each weighted by business impact. Score each dimension 0–100, then compute a weighted total.

| Dimension | Weight | Measures |
|-----------|--------|----------|
| **Correctness** | 30% | Does the system produce the right response for each intent? |
| **Security** | 25% | Are permissions, verification, and data isolation enforced? |
| **Performance** | 15% | Response latency, throughput, resource usage |
| **Coverage** | 15% | Test coverage across intent categories and edge cases |
| **Regression** | 15% | Does new work break existing functionality? |

## Scoring Rubrics

### Correctness (Weight: 30%)

Score based on intent-level accuracy:

| Score | Criteria |
|-------|----------|
| 95–100 | All 6 intents route correctly. Multi-intent decomposition accurate. Customer replies match ADR-0002 format. |
| 85–94 | All intents route correctly. Minor format drift in 1–2 replies. |
| 70–84 | 5/6 intents correct. One intent category routes to fallback. |
| 50–69 | 4/6 intents correct. Clear routing errors on ≥2 categories. |
| 0–49 | ≥3 intents misrouted. Protocol violations (missing `【客户回复】`). |

**Evidence required**: Run `tests/test_rag_customer_scenarios.py` and `tests/test_orchestrator_e2e.py`.

### Security (Weight: 25%)

Score based on verification and permission enforcement:

| Score | Criteria |
|-------|----------|
| 95–100 | OTP verification enforced. Scoped tokens checked. L0 agents never write. L2 agents never touch business systems. No credentials leaked in logs or responses. |
| 85–94 | All controls pass. One audit log edge case (e.g., token expiry not logged). |
| 70–84 | Verification works but edge case uncovered (e.g., expired token reused). No data leak. |
| 50–69 | Permission tier violated (L0 agent writes). No data exposure but control gap. |
| 0–49 | Token bypass possible. PII exposure risk. Sensitive path writable without verification. |

**Evidence required**: Run `tests/test_security_controls.py`.

### Performance (Weight: 15%)

| Score | Criteria |
|-------|----------|
| 95–100 | Orchestrator response < 2s p50, < 5s p95. MCP tool calls < 500ms p50. |
| 85–94 | Orchestrator < 3s p50, < 8s p95. |
| 70–84 | Orchestrator < 5s p50, < 15s p95. |
| 50–69 | Orchestrator < 10s p50. Timeouts on complex multi-agent calls. |
| 0–49 | Timeouts on simple queries. MCP server startup > 30s. |

**Evidence required**: `node scripts/harness/run-benchmark.mjs --html reports/benchmark.html`

### Coverage (Weight: 15%)

| Score | Criteria |
|-------|----------|
| 95–100 | Tests cover all 6 intents + multi-intent + escalation (L1→L2→L3) + satisfaction survey. Edge cases: empty input, extremely long input, mixed language, emoji-only. |
| 85–94 | All 6 intents covered. Escalation tested. 1–2 edge cases missing. |
| 70–84 | 5/6 intents covered. Escalation partially tested. |
| 50–69 | Core orchestration tested but individual sub-agents untested. |
| 0–49 | Only smoke tests. No intent-level coverage. |

**Evidence required**: `uv run pytest tests/ --cov --cov-report=term`

### Regression (Weight: 15%)

| Score | Criteria |
|-------|----------|
| 95–100 | Full suite passes. No skipped tests. Git bisect confirms no regressions in last 5 commits. |
| 85–94 | Full suite passes. 1–2 skipped tests with documented reason. |
| 70–84 | Suite passes. 3–5 skipped tests. |
| 50–69 | 1–2 failures in non-critical tests. |
| 0–49 | ≥3 failures or critical test failing. |

**Evidence required**: `uv run pytest tests/ -q --tb=short`

## Verification Stages

### Stage 1: Pre-Commit (Fast, < 30s)

Run before every commit:
```bash
bash init.sh --check-only --skip-tests
uv run pytest tests/test_smoke.py -q
```

Must pass: Correctness (smoke), Security (smoke), Regression (fast).

### Stage 2: Feature Gate (Medium, < 5min)

Run before marking a feature done:
```bash
bash init.sh
uv run pytest tests/ -q
node scripts/harness/validate-harness.mjs
```

Must pass: All dimensions at ≥ 70.

### Stage 3: Release Gate (Full, < 15min)

Run before merge to main:
```bash
bash init.sh
uv run pytest tests/ -q --cov --cov-report=html
node scripts/harness/validate-harness.mjs
node scripts/harness/run-benchmark.mjs --html reports/benchmark.html
node scripts/harness/render-assessment-html.mjs --output reports/harness-assessment.html
```

Must pass: All dimensions at ≥ 85.

## Verification Scorecard

Fill this out after each stage run:

```markdown
## Verification Run — YYYY-MM-DD

| Dimension | Score | Weight | Weighted | Evidence |
|-----------|-------|--------|----------|----------|
| Correctness | /100 | 30% | | `pytest tests/test_orchestrator_e2e.py` |
| Security | /100 | 25% | | `pytest tests/test_security_controls.py` |
| Performance | /100 | 15% | | `node scripts/harness/run-benchmark.mjs` |
| Coverage | /100 | 15% | | `pytest --cov` |
| Regression | /100 | 15% | | `pytest tests/` |

**Total**: __/100
**Stage**: Pre-Commit / Feature Gate / Release Gate
**Gate**: PASS / FAIL
**Actions**: (if fail, what needs fixing)
```

## Automated Scoring

Run the scoring script:
```bash
node scripts/harness/validate-harness.mjs --score
```

This produces a JSON scorecard:
```json
{
  "date": "2026-06-26",
  "scores": {
    "correctness": 92,
    "security": 88,
    "performance": 75,
    "coverage": 80,
    "regression": 95
  },
  "weighted_total": 86.9,
  "gate": "feature-gate",
  "passed": true,
  "warnings": ["Performance p95 exceeds 5s threshold"]
}
```

## Integration with CI

```yaml
# .github/workflows/verification.yml
name: Harness Verification
on: [push, pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install uv && uv sync
      - run: bash init.sh
      - run: node scripts/harness/validate-harness.mjs --score
      - run: node scripts/harness/run-benchmark.mjs --ci
```
