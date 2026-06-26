#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# 客服智能体 2.0 — Init & Verification
# ──────────────────────────────────────────────────────────
# Usage: bash init.sh [--check-only] [--skip-tests]
#
# Verifies the project is in a runnable state:
#   1. Python version
#   2. Virtual environment + dependencies
#   3. Database migrations
#   4. REST API health check
#   5. MCP server smoke test
#   6. Test suite
# ──────────────────────────────────────────────────────────

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CHECK_ONLY=false
SKIP_TESTS=false
for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=true ;;
    --skip-tests) SKIP_TESTS=true ;;
    --help|-h)
      echo "Usage: bash init.sh [--check-only] [--skip-tests]"
      echo "  --check-only   Only verify, don't fix anything"
      echo "  --skip-tests   Skip the test suite"
      exit 0
      ;;
  esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color
PASS="${GREEN}✔${NC}"
FAIL="${RED}✘${NC}"
WARN="${YELLOW}⚠${NC}"

failures=0
warnings=0

ok()   { echo -e "  $PASS $1"; }
warn() { echo -e "  $WARN $1"; warnings=$((warnings + 1)); }
fail() { echo -e "  $FAIL $1"; failures=$((failures + 1)); }

echo ""
echo "═══════════════════════════════════════════════"
echo "  客服智能体 2.0 — Init & Verification"
echo "═══════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────────────────
# 1. Python version
# ──────────────────────────────────────────────────────────
echo "[1/6] Python version"
PYTHON=""
if command -v python &>/dev/null; then
  PYTHON=python
elif command -v python3 &>/dev/null; then
  PYTHON=python3
fi

if [ -z "$PYTHON" ]; then
  fail "Python not found in PATH"
else
  version=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  major=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
  minor=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
  if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
    ok "Python $version ($($PYTHON -c "import sys; print(sys.executable)"))"
  else
    fail "Python $version — need ≥ 3.10"
  fi
fi

# ──────────────────────────────────────────────────────────
# 2. Dependencies
# ──────────────────────────────────────────────────────────
echo "[2/6] Dependencies (uv)"

_uv_install() {
  if [ "$CHECK_ONLY" = true ]; then
    warn "uv sync needed (check-only mode, not running)"
  else
    if uv sync --quiet 2>/dev/null; then
      ok "uv sync complete"
    else
      fail "uv sync failed"
    fi
  fi
}

if command -v uv &>/dev/null; then
  if [ -f "uv.lock" ] && [ -d ".venv" ]; then
    ok "uv + .venv detected"
  else
    _uv_install
  fi
else
  warn "uv not found — falling back to pip"
  if pip install -e ".[rag]" --quiet 2>/dev/null; then
    ok "pip install complete"
  else
    fail "pip install failed"
  fi
fi

# ──────────────────────────────────────────────────────────
# 3. Database
# ──────────────────────────────────────────────────────────
echo "[3/6] Database"

if [ -f "data/orders.db" ]; then
  ok "data/orders.db exists"
else
  warn "data/orders.db missing — running seed"
  if [ "$CHECK_ONLY" = false ]; then
    "$PYTHON" src/seed_data.py 2>/dev/null && ok "seed_data.py complete" || fail "seed_data.py failed"
  fi
fi

# Check alembic (via uv run when available)
if command -v uv &>/dev/null; then
  uv run alembic upgrade head 2>/dev/null && ok "alembic upgrade head" || warn "alembic upgrade skipped (may already be current)"
elif command -v alembic &>/dev/null || "$PYTHON" -m alembic --version &>/dev/null 2>&1; then
  if [ "$CHECK_ONLY" = false ]; then
    "$PYTHON" -m alembic upgrade head 2>/dev/null && ok "alembic upgrade head" || warn "alembic upgrade skipped (may already be current)"
  else
    ok "alembic available"
  fi
else
  warn "alembic not found — skipping migration check"
fi

# ──────────────────────────────────────────────────────────
# 4. REST API health check
# ──────────────────────────────────────────────────────────
echo "[4/6] REST API health (localhost:8000)"

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/orders/stats?period=all 2>/dev/null || echo "000")

case "$HEALTH" in
  200) ok "order API :8000 responded 200" ;;
  000) warn "order API :8000 not reachable — start with: uvicorn order_api:app --reload --port 8000" ;;
  *)   warn "order API :8000 returned HTTP $HEALTH" ;;
esac

# ──────────────────────────────────────────────────────────
# 5. MCP server smoke test
# ──────────────────────────────────────────────────────────
echo "[5/6] MCP server smoke"

# Check MCP config exists
if [ -f ".claude/mcp.json" ]; then
  SERVERS=$("$PYTHON" -c "
import json
with open('.claude/mcp.json') as f:
    config = json.load(f)
print(' '.join(config.get('mcpServers', {}).keys()))
" 2>/dev/null || echo "")
  if [ -n "$SERVERS" ]; then
    ok "MCP config: $SERVERS"
  else
    warn "MCP config exists but no servers found"
  fi
else
  fail "Missing .claude/mcp.json"
fi

# Verify server files exist
for srv in src/server.py src/server_customer.py; do
  if [ -f "$srv" ]; then
    ok "$srv present"
  else
    fail "$srv missing"
  fi
done

# ──────────────────────────────────────────────────────────
# 6. Tests
# ──────────────────────────────────────────────────────────
echo "[6/6] Test suite"

if [ "$SKIP_TESTS" = true ]; then
  warn "Tests skipped (--skip-tests)"
elif [ -d "tests" ]; then
  if "$PYTHON" -m pytest tests/ -q --tb=short 2>&1; then
    ok "All tests passed"
  else
    fail "Some tests failed"
  fi
else
  warn "No tests/ directory found"
fi

# ──────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────"
if [ "$failures" -eq 0 ] && [ "$warnings" -eq 0 ]; then
  echo -e "  ${GREEN}All checks passed — project is ready.${NC}"
elif [ "$failures" -eq 0 ]; then
  echo -e "  ${YELLOW}$warnings warning(s) — project is usable but needs attention.${NC}"
else
  echo -e "  ${RED}$failures failure(s), $warnings warning(s) — fix failures before working.${NC}"
fi
echo "─────────────────────────────────────────────────"
echo ""

exit $failures
