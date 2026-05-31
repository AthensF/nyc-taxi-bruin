#!/usr/bin/env bash
# One-command self-healing ELT demo.
# First run builds .venv + installs deps; later runs are instant.
# Keys come from .env (ANTHROPIC_API_KEY required; WANDB_API_KEY optional for the Weave trace).
#
#   ./demo.sh              # inject drift -> agents heal -> verify -> reset
#   ./demo.sh --no-reset   # leave the pipeline mutated for inspection
set -euo pipefail
cd "$(dirname "$0")"
VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
  echo "[demo] first run: creating $VENV + installing deps (a minute or two)…"
  python3.12 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip >/dev/null
  "$VENV/bin/pip" install -q -r agents/requirements.txt
fi
exec "$VENV/bin/python" -m agents.run_demo "$@"
