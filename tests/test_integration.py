"""Merge gate — proves the parallel parts fit (Foundation-owned, FROZEN intent).

The cohesion guarantee: Part B builds against `FakeGateway`, so the gate proves
that fake is faithful to Part A's REAL pipeline. Tests degrade gracefully — the
real-pipeline and end-to-end tests SKIP until A/B land and keys exist, so this
file is green from the Foundation commit onward.

Run subsets:
  pytest tests/test_integration.py                      # contract + fake-faithfulness (always)
  pytest tests/test_integration.py -m parity            # + real-vs-fake (needs duckdb + bruin + A)
  pytest tests/test_integration.py -m "integration and llm"  # + e2e heal (needs A + B + keys)
"""
import os
import shutil
import subprocess

import pytest

from agents import contracts as C
from agents.contracts import COLUMN_RENAME, FakeGateway, ORACLE_SQL


# --------------------------------------------------------------------------- #
# 1. Contract is importable and complete (always runs)                        #
# --------------------------------------------------------------------------- #
def test_contract_surface_present():
    for name in (
        "RepairState", "ToolResult", "Column", "ToolGateway",
        "Diagnosis", "TestSpec", "FixCandidate", "ReviewVerdict",
        "VerifyResult", "Authorization", "DriftScenario", "SCENARIOS",
        "ORACLE_SQL", "FakeGateway",
    ):
        assert hasattr(C, name), f"contracts.py is missing {name} — frozen seam broken"


def test_scenario_constants():
    assert COLUMN_RENAME.renamed_from == "payment_type"
    assert COLUMN_RENAME.renamed_to == "payment_method"
    assert COLUMN_RENAME.expected_error_substring.lower() in COLUMN_RENAME.inject_sql.lower() \
        or COLUMN_RENAME.expected_error_substring == "payment_type"


# --------------------------------------------------------------------------- #
# 2. FakeGateway is faithful to the CONTRACT (always runs, no real DB)         #
# --------------------------------------------------------------------------- #
def test_fake_starts_broken_with_expected_error():
    fake = FakeGateway()
    res = fake.run_bruin(COLUMN_RENAME.staging_asset_path)
    assert res["ok"] is False
    assert COLUMN_RENAME.expected_error_substring.lower() in res["stderr"].lower()


def test_fake_schema_shows_drift():
    cols = {c["column"].lower() for c in FakeGateway().inspect_schema(COLUMN_RENAME.upstream_table)}
    assert "payment_method" in cols
    assert "payment_type" not in cols


def test_fake_heals_on_correct_patch_and_rejects_half_fix():
    fake = FakeGateway()
    # half-fix: projection updated but JOIN still references payment_type -> still red
    half = "SELECT PAYMENT_METHOD as payment_type FROM x JOIN y ON d.PAYMENT_TYPE = p.id"
    fake.apply_patch(COLUMN_RENAME.staging_asset_path, half)
    assert fake.run_bruin("x")["ok"] is False
    # full fix: no bare payment_type, uses payment_method -> green + oracle answers
    full = "SELECT PAYMENT_METHOD as payment_method, payment_type_name FROM x JOIN y ON d.PAYMENT_METHOD = p.id"
    fake.apply_patch(COLUMN_RENAME.staging_asset_path, full)
    assert fake.run_bruin("x")["ok"] is True
    row = fake.oracle()
    assert row and row["gross_revenue"] > 0


# --------------------------------------------------------------------------- #
# 3. Parity: the REAL pipeline matches the fake (needs duckdb + bruin + A)     #
# --------------------------------------------------------------------------- #
def _have_real_pipeline() -> bool:
    return (
        C.DUCKDB_PATH.exists()
        and shutil.which("bruin") is not None
        and (C.REPO_ROOT / "scripts" / "inject_drift.sql").exists()
        and (C.REPO_ROOT / "scripts" / "reset.sh").exists()
    )


@pytest.mark.parity
def test_real_error_matches_fake_substring():
    if not _have_real_pipeline():
        pytest.skip("real pipeline not ready (need duckdb.db, bruin, scripts/ from Part A)")
    # Part A's reset.sh must leave a clean baseline afterwards.
    inject = C.REPO_ROOT / "scripts" / "inject_drift.sql"
    reset = C.REPO_ROOT / "scripts" / "reset.sh"
    try:
        subprocess.run(["duckdb", str(C.DUCKDB_PATH)], stdin=open(inject), cwd=C.REPO_ROOT,
                       capture_output=True, text=True, timeout=60)
        proc = subprocess.run(
            ["bruin", "run", str(C.STAGING_ASSET_PATH), "--downstream"],
            cwd=C.REPO_ROOT, capture_output=True, text=True, timeout=300,
        )
        blob = (proc.stdout + proc.stderr).lower()
        assert proc.returncode != 0, "real pipeline should be RED after inject"
        assert COLUMN_RENAME.expected_error_substring.lower() in blob, \
            "FakeGateway error substring is NOT faithful to the real pipeline error"
    finally:
        subprocess.run(["bash", str(reset)], cwd=C.REPO_ROOT, capture_output=True, text=True, timeout=300)


# --------------------------------------------------------------------------- #
# 4. End-to-end heal (needs A + B + ANTHROPIC_API_KEY)                         #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.llm
def test_end_to_end_heal():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY")
    if not (C.REPO_ROOT / "agents" / "run_demo.py").exists() or not (C.REPO_ROOT / "agents" / "tools.py").exists():
        pytest.skip("Part B not ready (need agents/run_demo.py + agents/tools.py)")
    if not _have_real_pipeline():
        pytest.skip("Part A not ready")
    from agents import run_demo  # noqa: F401  (presence is the integration check)
    result = run_demo.main()  # expected to inject, heal, verify, reset
    assert result is not None and result.get("final_status") == "healed"
    assert result.get("verify") and result["verify"].healed
