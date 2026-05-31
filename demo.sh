#!/usr/bin/env bash
# One-command self-healing ELT demo.
# Keys come from .env (ANTHROPIC_API_KEY required; WANDB_API_KEY optional for the Weave trace).
#
#   ./demo.sh              # inject drift -> agents heal -> verify -> reset
#   ./demo.sh --no-reset   # leave the pipeline mutated for inspection
#   QUALITY_MIN=9 ./demo.sh  # require quality score >= 9 (forces re-fix if below)
set -euo pipefail
cd "$(dirname "$0")"
VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
  echo "[demo] first run: creating $VENV + installing deps (a minute or two)…"
  python3.12 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip >/dev/null
  "$VENV/bin/pip" install -q -r agents/requirements.txt
fi

# Quality gate: set QUALITY_MIN to enforce a minimum quality score (0 = off)
export QUALITY_MIN="${QUALITY_MIN:-0}"
if [ "$QUALITY_MIN" -gt 0 ]; then
  echo "[demo] quality gate: QUALITY_MIN=$QUALITY_MIN (will re-fix if quality < $QUALITY_MIN)"
fi

exec "$VENV/bin/python" -m agents.run_demo "$@"
