#!/usr/bin/env bash
# Merge gate — one command, one verdict. Proves the parallel parts fit.
# Owned by the Foundation step; each chat runs it before declaring done, and the
# human runs it once on the integration branch.
#
#   bash scripts/check_merge.sh
#
# Exit code is non-zero if any REQUIRED step fails. Tests that need pieces not yet
# built (real pipeline, Part B, API keys) SKIP rather than fail.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

FAIL=0
pass() { printf '  \033[32m✅ %s\033[0m\n' "$1"; }
fail() { printf '  \033[31m❌ %s\033[0m\n' "$1"; FAIL=1; }
skip() { printf '  \033[33m⏭️  %s\033[0m\n' "$1"; }

PYTEST=(python -m pytest -q)

echo "== Merge gate =="

# 1. Frozen contract unchanged vs main -------------------------------------- #
echo "[1] contract frozen (agents/contracts.py unchanged vs main)"
if git rev-parse --verify -q main >/dev/null; then
  if git diff --quiet main -- agents/contracts.py; then
    pass "contracts.py matches main"
  else
    fail "contracts.py was edited — the seam is frozen; route the change through the integrator"
  fi
else
  skip "no 'main' ref to diff against (are you on main with no branches yet?)"
fi

# 2. Part A DoD ------------------------------------------------------------- #
echo "[2] Part A — scenario tests"
if [ -f tests/test_scenario.py ]; then
  if "${PYTEST[@]}" tests/test_scenario.py; then pass "tests/test_scenario.py"; else fail "tests/test_scenario.py"; fi
else
  skip "tests/test_scenario.py not present yet (Part A)"
fi

# 3. Part B DoD ------------------------------------------------------------- #
echo "[3] Part B — agent tests (non-llm)"
if [ -f tests/test_agents.py ]; then
  if "${PYTEST[@]}" tests/test_agents.py -m "not llm"; then pass "tests/test_agents.py -m 'not llm'"; else fail "tests/test_agents.py"; fi
else
  skip "tests/test_agents.py not present yet (Part B)"
fi

# 4. Seam parity + contract surface (always meaningful) --------------------- #
echo "[4] seam parity + contract surface"
if "${PYTEST[@]}" tests/test_integration.py -m "not integration"; then
  pass "contract + fake-faithfulness (+ parity if real pipeline present)"
else
  fail "tests/test_integration.py (seam parity)"
fi

# 5. End-to-end heal (best effort) ----------------------------------------- #
echo "[5] end-to-end heal (needs A + B + ANTHROPIC_API_KEY)"
if "${PYTEST[@]}" tests/test_integration.py -m "integration and llm"; then
  pass "end-to-end heal (or skipped if prerequisites missing)"
else
  fail "end-to-end heal"
fi

echo "================"
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32mMERGEABLE ✅\033[0m\n'; exit 0
else
  printf '\033[31mNOT MERGEABLE ❌ — see failures above\033[0m\n'; exit 1
fi
