"""Part A · scenario tests — the break is replicable, reversible, and the oracle works.

These run against the REAL pipeline (duckdb.db + bruin), so they are slow and
mutate state. A module-scoped fixture guarantees baseline-green before and after
(even on failure). Tests SKIP gracefully if duckdb/bruin/duckdb.db are absent,
mirroring tests/test_integration.py.

Contract honored (PLAN.md › Parallelization › Part A):
  * after inject, `bruin run <staging> --downstream` fails with stderr/stdout
    containing the scenario's expected_error_substring ("payment_type")
  * after reset it is green
  * the oracle returns a valid (date, gross_revenue>0) row on healthy data
"""
import datetime as dt
import json
import shutil
import subprocess

import pytest

from agents import contracts as C
from agents.contracts import COLUMN_RENAME, ORACLE_SQL

BRUIN_TIMEOUT = 600
INJECT_SQL = C.REPO_ROOT / "scripts" / "inject_drift.sql"
RESET_SH = C.REPO_ROOT / "scripts" / "reset.sh"


def _have_pipeline() -> bool:
    return (
        C.DUCKDB_PATH.exists()
        and shutil.which("bruin") is not None
        and shutil.which("duckdb") is not None
        and INJECT_SQL.exists()
        and RESET_SH.exists()
    )


pytestmark = pytest.mark.skipif(
    not _have_pipeline(),
    reason="real pipeline not ready (need duckdb.db, bruin, duckdb CLI, scripts/)",
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _run_inject() -> None:
    with open(INJECT_SQL) as f:
        subprocess.run(
            ["duckdb", str(C.DUCKDB_PATH)], stdin=f, cwd=C.REPO_ROOT,
            capture_output=True, text=True, timeout=120, check=True,
        )


def _run_reset() -> None:
    subprocess.run(
        ["bash", str(RESET_SH)], cwd=C.REPO_ROOT,
        capture_output=True, text=True, timeout=BRUIN_TIMEOUT, check=True,
    )


def _run_bruin_downstream() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bruin", "run", str(C.STAGING_ASSET_PATH), "--downstream"],
        cwd=C.REPO_ROOT, capture_output=True, text=True, timeout=BRUIN_TIMEOUT,
    )


def _duckdb_json(sql: str) -> list[dict]:
    proc = subprocess.run(
        ["duckdb", str(C.DUCKDB_PATH), "-json", sql],
        cwd=C.REPO_ROOT, capture_output=True, text=True, timeout=120, check=True,
    )
    out = proc.stdout.strip()
    return json.loads(out) if out else []


# --------------------------------------------------------------------------- #
# Fixture — guarantee baseline green at module start and teardown             #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module", autouse=True)
def baseline_green():
    _run_reset()          # ensure we start clean (idempotent)
    yield
    _run_reset()          # always leave the demo green


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #
def test_oracle_valid_on_healthy_data():
    """The business done-heuristic returns one (date, gross>0) row in-month."""
    rows = _duckdb_json(ORACLE_SQL)
    assert len(rows) == 1, f"oracle should return exactly one row, got {rows}"
    row = rows[0]
    assert row["gross_revenue"] > 0
    pickup = dt.date.fromisoformat(str(row["pickup_date"]))
    assert pickup.year == 2022 and pickup.month == 1, f"date out of loaded month: {pickup}"


def test_inject_breaks_downstream():
    """After inject, the downstream run is RED and names the renamed column."""
    try:
        _run_inject()
        proc = _run_bruin_downstream()
        blob = (proc.stdout + proc.stderr).lower()
        assert proc.returncode != 0, "pipeline should be RED after inject"
        assert COLUMN_RENAME.expected_error_substring.lower() in blob, \
            f"expected '{COLUMN_RENAME.expected_error_substring}' in bruin output"
    finally:
        _run_reset()


def test_reset_restores_green():
    """reset.sh restores a green pipeline WITH data (idempotent).

    reset.sh runs `bruin run ... --downstream` internally with check=True, so a
    non-zero (red) bruin exit raises here — that is the green proof. We also
    assert the oracle answers, proving the data (not just an empty-but-valid
    pipeline) was rebuilt over the seeded window.
    """
    _run_reset()          # raises if bruin is red
    rows = _duckdb_json(ORACLE_SQL)
    assert len(rows) == 1 and rows[0]["gross_revenue"] > 0, \
        f"reset should leave a populated, green pipeline; oracle gave {rows}"
