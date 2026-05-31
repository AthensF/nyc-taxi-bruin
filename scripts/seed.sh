#!/usr/bin/env bash
# Part A · seed DuckDB once (no-op if already seeded).
#
# The demo runs against a pre-seeded duckdb.db. This script only seeds when
# ingestion.trips is missing or empty, so it is safe to run repeatedly and will
# NOT clobber an existing 182MB seeded DB during development.
#
# Seeds a tiny deterministic window (Jan 2022, yellow only) per PLAN.md.
#
# Usage:  bash scripts/seed.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DB="$REPO_ROOT/duckdb.db"
PIPELINE="$REPO_ROOT/zoomcamp/pipeline/pipeline.yml"

NEEDS_SEED=1
if [ -f "$DB" ]; then
  # table exists and has rows?
  ROWS="$(duckdb "$DB" -noheader -list \
    "SELECT count(*) FROM information_schema.tables \
     WHERE table_schema='ingestion' AND table_name='trips';" 2>/dev/null || echo 0)"
  if [ "${ROWS:-0}" = "1" ]; then
    N="$(duckdb "$DB" -noheader -list "SELECT count(*) FROM ingestion.trips;" 2>/dev/null || echo 0)"
    if [ "${N:-0}" -gt 0 ] 2>/dev/null; then
      NEEDS_SEED=0
    fi
  fi
fi

if [ "$NEEDS_SEED" = "0" ]; then
  echo "[seed] ingestion.trips already populated — skipping seed"
  exit 0
fi

echo "[seed] seeding DuckDB (Jan 2022, yellow) — this re-downloads parquet, may be slow"
bruin run "$PIPELINE" \
  --start-date 2022-01-01 --end-date 2022-02-01 \
  --var taxi_types='["yellow"]'

echo "[seed] done"
