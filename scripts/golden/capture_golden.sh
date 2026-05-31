#!/usr/bin/env bash
# Part A · capture the known-good reports.trips_report snapshot.
#
# Writes a deterministic (stable-ordered) CSV to scripts/golden/expected_report.csv
# as regression ground truth for the (stretch) Regression Guard + integration test IT4.
# Run this only against a HEALTHY pipeline (baseline green, pre-drift).
#
# Usage:  bash scripts/golden/capture_golden.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB="$REPO_ROOT/duckdb.db"
OUT="$REPO_ROOT/scripts/golden/expected_report.csv"

echo "[golden] exporting reports.trips_report -> $OUT"
duckdb "$DB" "COPY (
    SELECT *
    FROM reports.trips_report
    ORDER BY pickup_date, taxi_type, payment_type_name
  ) TO '$OUT' (HEADER, DELIMITER ',');"

echo "[golden] captured $(($(wc -l < "$OUT") - 1)) rows"
