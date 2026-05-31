"""Part B tests — each agent judged against its goal, plus the orchestration loop.

Deterministic tests use scripted agent policies + the FakeGateway (no API key, no real
DB), so they run in any minimal env. LLM-backed behaviour is covered by @pytest.mark.llm
tests that skip without ANTHROPIC_API_KEY.
"""
import os
import re

import pytest

from agents.contracts import (
    COLUMN_RENAME,
    STAGING_ASSET_PATH,
    Authorization,
    Diagnosis,
    FakeGateway,
    FixCandidate,
    QualityScore,
    ReviewVerdict,
    VerifyResult,
)
from agents.contracts import TestSpec as _TestSpec  # aliased so pytest doesn't collect it
from agents.graph import Deps, route_after_review, run_repair

# --- representative SQL candidates -------------------------------------------------- #
GOOD_SQL = (
    "SELECT PAYMENT_METHOD as payment_method, "
    "COALESCE(p.payment_type_name,'unknown') as payment_type_name "
    "FROM deduplicated d LEFT JOIN ingestion.payment_lookup p "
    "ON d.PAYMENT_METHOD = p.payment_type_id"
)
HALF_SQL = (  # projection fixed but JOIN still references the old column -> still red
    "SELECT PAYMENT_METHOD as payment_type, p.payment_type_name "
    "FROM deduplicated d LEFT JOIN ingestion.payment_lookup p "
    "ON d.PAYMENT_TYPE = p.payment_type_id"
)
DEAD_SQL = (  # compiles, but a redundant fallback on the renamed column (quality issue)
    "SELECT COALESCE(PAYMENT_METHOD, 0) as payment_method, p.payment_type_name "
    "FROM deduplicated d LEFT JOIN ingestion.payment_lookup p "
    "ON d.PAYMENT_METHOD = p.payment_type_id"
)

_BARE = re.compile(r"payment_type(?![_a-zA-Z])", re.IGNORECASE)  # bare col, not payment_type_id/_name


# --- scripted agent policies -------------------------------------------------------- #
def scripted_diagnoser(evidence, state):
    diag = Diagnosis(
        root_cause="payment_type renamed to payment_method",
        why="the upstream rename orphaned the payment-lookup JOIN key",
        evidence=[evidence.get("error", "")[:80]],
        references=list(COLUMN_RENAME.references_to_update),
        acceptance_criteria=["no bare payment_type refs", "preserve payment_type_name",
                             "oracle returns a valid row"],
    )
    return diag, _TestSpec(structural_assertions=["no bare payment_type", "uses payment_method"])


def make_scripted_fixer(sequence):
    def fixer(state, attempt_n):
        sql = sequence[min(attempt_n - 1, len(sequence) - 1)]
        return FixCandidate(attempt_n=attempt_n, sql=sql, summary="scripted",
                            files_changed=["staging/trips.sql"], blast_radius="low")
    return fixer


def scripted_reviewer(state) -> ReviewVerdict:
    sql = state["fix"].sql
    if _BARE.search(sql):
        return ReviewVerdict("reject", confidence=0.9,
                             rationale="JOIN still references PAYMENT_TYPE",
                             correctness_findings=["bare payment_type reference remains"])
    if "coalesce(payment_method" in sql.lower().replace(" ", ""):
        return ReviewVerdict("reject", confidence=0.8,
                             rationale="dead-code fallback on a renamed column",
                             quality_findings=["redundant COALESCE on payment_method"])
    return ReviewVerdict("approve", confidence=0.92, rationale="minimal, correct")


def scripted_quality_assessor(state) -> QualityScore:
    return QualityScore(overall=8, rationale="minimal, idiomatic; preserves the lookup contract",
                        criteria={"minimality": {"score": 9, "note": "~2-line change"}})


def _initial():
    return {
        "staging_asset_path": str(STAGING_ASSET_PATH),
        "upstream_table": COLUMN_RENAME.upstream_table,
        "scenario": COLUMN_RENAME.name,
        "max_attempts": 3,
    }


def _deps(sequence, max_attempts=3):
    return Deps(gateway=FakeGateway(), diagnoser=scripted_diagnoser,
               fixer=make_scripted_fixer(sequence), reviewer=scripted_reviewer,
               max_attempts=max_attempts)


# --- Reviewer (the veto is the point) ---------------------------------------------- #
def test_reviewer_approves_good():
    assert scripted_reviewer({"fix": FixCandidate(1, GOOD_SQL)}).decision == "approve"


def test_reviewer_rejects_half_fix():
    v = scripted_reviewer({"fix": FixCandidate(1, HALF_SQL)})
    assert v.decision == "reject" and v.correctness_findings


def test_reviewer_rejects_deadcode_on_quality():
    v = scripted_reviewer({"fix": FixCandidate(1, DEAD_SQL)})
    assert v.decision == "reject" and v.quality_findings


# --- routing ------------------------------------------------------------------------ #
def test_routing_approve_goes_to_deploy():
    d = _deps([GOOD_SQL], max_attempts=3)
    assert route_after_review({"review": ReviewVerdict("approve"), "attempts": 1}, d) == "deploy"


def test_routing_reject_loops_then_gives_up():
    d = _deps([HALF_SQL], max_attempts=2)
    assert route_after_review({"review": ReviewVerdict("reject"), "attempts": 1}, d) == "fix"
    assert route_after_review({"review": ReviewVerdict("reject"), "attempts": 2}, d) == "needs_human"


# --- orchestration end-to-end (FakeGateway) ----------------------------------------- #
def test_run_repair_converges_after_one_reject():
    final = run_repair(_initial(), _deps([HALF_SQL, GOOD_SQL], max_attempts=3))
    assert final["final_status"] == "healed"
    assert final["attempts"] == 2                      # rejected once, fixed on 2nd
    assert final["review"].decision == "approve"
    assert final["verify"].healed is True
    assert final["verify"].oracle_answer["gross_revenue"] > 0


def test_run_repair_gives_up_without_deploying():
    final = run_repair(_initial(), _deps([HALF_SQL], max_attempts=2))
    assert final["final_status"] == "needs_human"
    assert "authorization" not in final                # never deployed
    assert "verify" not in final                       # never verified


def test_milestones_emitted_as_typed_artifacts():
    final = run_repair(_initial(), _deps([HALF_SQL, GOOD_SQL], max_attempts=3))
    assert isinstance(final["diagnosis"], Diagnosis)
    assert isinstance(final["test_spec"], _TestSpec)
    assert isinstance(final["fix"], FixCandidate)
    assert isinstance(final["review"], ReviewVerdict)
    assert isinstance(final["verify"], VerifyResult)
    assert isinstance(final["authorization"], Authorization)
    assert final["authorization"].audit_id            # has an audit id


def test_quality_scorecard_emitted_after_heal():
    deps = _deps([HALF_SQL, GOOD_SQL], max_attempts=3)
    deps.quality_assessor = scripted_quality_assessor
    final = run_repair(_initial(), deps)
    q = final["quality"]
    assert isinstance(q, QualityScore) and 1 <= q.overall <= 10


# --- LLM-backed smoke (skipped without a key) --------------------------------------- #
@pytest.mark.llm
def test_llm_reviewer_rejects_half_fix():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY")
    pytest.importorskip("langchain_anthropic")
    from agents import llm
    state = {
        "staging_asset_path": str(STAGING_ASSET_PATH),
        "diagnosis": Diagnosis(root_cause="rename", why="", acceptance_criteria=["no bare payment_type"]),
        "fix": FixCandidate(1, HALF_SQL),
    }
    assert llm.reviewer(state).decision == "reject"
