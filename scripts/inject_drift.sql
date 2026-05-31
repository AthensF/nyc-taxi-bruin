-- Part A · drift injection (column_rename scenario)
-- Simulates "a new upstream load arrived with a renamed column".
-- Renames ingestion.trips.payment_type -> payment_method so staging.trips
-- breaks with: Binder Error: Referenced column "PAYMENT_TYPE" not found.
--
-- Mirrors agents.contracts.COLUMN_RENAME.inject_sql (the frozen contract).
-- Run with:  duckdb duckdb.db < scripts/inject_drift.sql
ALTER TABLE ingestion.trips RENAME COLUMN payment_type TO payment_method;
