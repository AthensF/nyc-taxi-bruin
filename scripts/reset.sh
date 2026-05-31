#!/usr/bin/env bash
# Part A · reset to baseline green (idempotent — safe to re-run any number of times).
#
# Undoes the drift demo:
#   1. Restore the baseline staging SQL the agent may have patched (git checkout).
#   2. Rename ingestion.trips.payment_method -> payment_type IFF it is currently
#      renamed (DuckDB has no RENAME COLUMN IF EXISTS, so we probe first).
#   3. Rebuild downstream (staging.trips + reports.trips_report) to green.
#
# Usage:  bash scripts/reset.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DB="$REPO_ROOT/duckdb.db"
STAGING_ASSET="zoomcamp/pipeline/assets/staging/trips.sql"

echo "[reset] restoring baseline staging SQL ($STAGING_ASSET)"
git checkout -- "$STAGING_ASSET"

# Idempotent column rename-back: only if 'payment_method' exists and
# 'payment_type' does not (i.e. drift is currently injected).
HAS_METHOD="$(duckdb "$DB" -noheader -list \
  "SELECT count(*) FROM information_schema.columns \
   WHERE table_schema='ingestion' AND table_name='trips' \
     AND column_name='payment_method';")"
HAS_TYPE="$(duckdb "$DB" -noheader -list \
  "SELECT count(*) FROM information_schema.columns \
   WHERE table_schema='ingestion' AND table_name='trips' \
     AND column_name='payment_type';")"

if [ "$HAS_METHOD" = "1" ] && [ "$HAS_TYPE" = "0" ]; then
  echo "[reset] reverting column rename payment_method -> payment_type"
  duckdb "$DB" "ALTER TABLE ingestion.trips RENAME COLUMN payment_method TO payment_type;"
else
  echo "[reset] column already baseline (payment_type present) — skipping rename"
fi

echo "[reset] rebuilding downstream (staging.trips + reports.trips_report)"
# Rebuild over the seeded window (Jan 2022). Without an explicit window bruin
# uses the schedule default, which is outside the loaded data and yields 0 rows.
bruin run "$REPO_ROOT/$STAGING_ASSET" --downstream \
  --start-date 2022-01-01 --end-date 2022-02-01

echo "[reset] done — baseline green restored"
