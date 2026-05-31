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


def scripted_fixer_pool(state, attempt_n, n=3):
    """Generate N scripted candidates in parallel (simulating diversity)."""
    # Return a pool of candidates: [GOOD_SQL, HALF_SQL, DEAD_SQL] for diversity
    candidates = []
    for i in range(n):
        if i == 0:
            sql = GOOD_SQL
            summary = "conservative fix"
        elif i == 1:
            sql = HALF_SQL
            summary = "partial fix"
        else:
            sql = DEAD_SQL
            summary = "fix with dead code"
        candidates.append(FixCandidate(
            attempt_n=attempt_n,
            sql=sql,
            summary=summary,
            files_changed=["staging/trips.sql"],
            blast_radius="low",
        ))
    return candidates


def scripted_picker(state, candidates):
    """Select the best candidate based on correctness and quality."""
    from agents.contracts import PickerVerdict
    
    # Find the first candidate without bare payment_type refs and without dead code
    for i, c in enumerate(candidates):
        sql = c.sql.lower().replace(" ", "")
        if not _BARE.search(c.sql) and "coalesce(payment_method" not in sql:
            return PickerVerdict(
                selected_index=i,
                rationale=f"Candidate {i} is correct and minimal",
                evaluation=[{"index": j, "summary": f"Candidate {j} evaluated"} for j in range(len(candidates))],
            )
    
    # Fallback: pick the first one
    return PickerVerdict(
        selected_index=0,
        rationale="No ideal candidate; selecting first",
        evaluation=[{"index": j, "summary": f"Candidate {j} evaluated"} for j in range(len(candidates))],
    )


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
    return Deps(
        gateway=FakeGateway(),
        diagnoser=scripted_diagnoser,
        fixer=make_scripted_fixer(sequence),
        fixer_pool=scripted_fixer_pool,
        picker=scripted_picker,
        reviewer=scripted_reviewer,
        max_attempts=max_attempts,
    )


# --- Best-of-N Fixers tests --------------------------------------------------------- #
def test_fixer_pool_generates_n_candidates():
    """Test that fixer_pool generates N diverse candidates."""
    from agents.graph import fix_pool
    
    state = _initial()
    state["diagnosis"] = Diagnosis(
        root_cause="test",
        why="test",
        acceptance_criteria=["test"]
    )
    deps = _deps([GOOD_SQL])
    
    result = fix_pool(state, deps)
    assert "candidates" in result
    assert len(result["candidates"]) == 3  # Default pool size
    assert all(isinstance(c, FixCandidate) for c in result["candidates"])


def test_picker_selects_best_candidate():
    """Test that picker selects the best candidate from the pool."""
    from agents.graph import pick_best
    from agents.contracts import PickerVerdict
    
    state = _initial()
    state["diagnosis"] = Diagnosis(
        root_cause="test",
        why="test",
        acceptance_criteria=["test"]
    )
    # Manually set candidates for testing
    state["candidates"] = [
        FixCandidate(1, HALF_SQL, summary="bad"),
        FixCandidate(1, GOOD_SQL, summary="good"),
        FixCandidate(1, DEAD_SQL, summary="dead code"),
    ]
    deps = _deps([GOOD_SQL])
    
    result = pick_best(state, deps)
    assert "picker_verdict" in result
    assert isinstance(result["picker_verdict"], PickerVerdict)
    assert "fix" in result  # Selected candidate becomes the active fix
    # Should select the good SQL (index 1)
    assert result["picker_verdict"].selected_index == 1
    assert result["fix"].sql == GOOD_SQL


def test_run_repair_with_fixer_pool_converges_faster():
    """Test that best-of-n fixers converges in fewer attempts."""
    # With pool of 3, the first round should find GOOD_SQL and approve
    final = run_repair(_initial(), _deps([GOOD_SQL], max_attempts=3))
    assert final["final_status"] == "healed"
    assert final["attempts"] == 1  # Should succeed on first attempt with pool
    assert "candidates" in final
    assert len(final["candidates"]) == 3
    assert "picker_verdict" in final


def test_run_repair_pool_retries_on_rejection():
    """Test that pool retries when all candidates are rejected."""
    # Create a custom fixer pool that only returns bad candidates
    def bad_fixer_pool(state, attempt_n, n=3):
        return [
            FixCandidate(attempt_n=attempt_n, sql=HALF_SQL, summary="bad",
                        files_changed=["staging/trips.sql"], blast_radius="low")
            for _ in range(n)
        ]
    
    # Custom reviewer that always rejects
    def always_reject_reviewer(state):
        return ReviewVerdict("reject", confidence=1.0,
                           rationale="always reject for testing")
    
    deps = Deps(
        gateway=FakeGateway(),
        diagnoser=scripted_diagnoser,
        fixer=make_scripted_fixer([HALF_SQL]),
        fixer_pool=bad_fixer_pool,
        picker=scripted_picker,
        reviewer=always_reject_reviewer,
        max_attempts=2,
    )
    
    final = run_repair(_initial(), deps)
    assert final["final_status"] == "needs_human"
    assert final["attempts"] >= 1  # At least one retry happened


def test_picker_verdict_is_typed_artifact():
    """Test that picker verdict is emitted as a typed milestone."""
    from agents.contracts import PickerVerdict
    
    state = _initial()
    state["diagnosis"] = Diagnosis(
        root_cause="test",
        why="test",
        acceptance_criteria=["test"]
    )
    state["candidates"] = [FixCandidate(1, GOOD_SQL)]
    deps = _deps([GOOD_SQL])
    
    result = run_repair(state, deps)
    assert "picker_verdict" in result
    assert isinstance(result["picker_verdict"], PickerVerdict)


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


def test_routing_reject_loops_to_fix_pool():
    """Best-of-n: rejection routes back to fix_pool (not fix)."""
    d = _deps([HALF_SQL], max_attempts=2)
    assert route_after_review({"review": ReviewVerdict("reject"), "attempts": 1}, d) == "fix_pool"
    assert route_after_review({"review": ReviewVerdict("reject"), "attempts": 2}, d) == "needs_human"


# --- orchestration end-to-end (FakeGateway) ----------------------------------------- #
def test_run_repair_converges_with_pool():
    """Best-of-n: pool generates diverse candidates, picker selects winner, converges fast."""
    final = run_repair(_initial(), _deps([GOOD_SQL], max_attempts=3))
    assert final["final_status"] == "healed"
    assert final["attempts"] == 1                      # Pool finds good candidate on first try
    assert "candidates" in final
    assert len(final["candidates"]) == 3               # N candidates generated
    assert final["review"].decision == "approve"
    assert final["verify"].healed is True
    assert final["verify"].oracle_answer["gross_revenue"] > 0


def test_run_repair_gives_up_when_all_candidates_rejected():
    """Test that system gives up when max_attempts is exceeded."""
    # Custom fixer pool that only returns bad candidates
    def bad_fixer_pool(state, attempt_n, n=3):
        return [
            FixCandidate(attempt_n=attempt_n, sql=HALF_SQL, summary="bad",
                        files_changed=["staging/trips.sql"], blast_radius="low")
            for _ in range(n)
        ]
    
    # Custom reviewer that always rejects
    def always_reject_reviewer(state):
        return ReviewVerdict("reject", confidence=1.0,
                           rationale="always reject for testing")
    
    # Custom picker that always picks index 0
    def dummy_picker(state, candidates):
        from agents.contracts import PickerVerdict
        return PickerVerdict(
            selected_index=0,
            rationale="picking first candidate",
            evaluation=[{"index": i, "summary": f"Candidate {i}"} for i in range(len(candidates))],
        )
    
    deps = Deps(
        gateway=FakeGateway(),
        diagnoser=scripted_diagnoser,
        fixer=make_scripted_fixer([HALF_SQL]),
        fixer_pool=bad_fixer_pool,
        picker=dummy_picker,
        reviewer=always_reject_reviewer,
        max_attempts=2,
    )
    
    final = run_repair(_initial(), deps)
    # Should give up after max_attempts
    assert final["final_status"] == "needs_human"
    assert final["attempts"] >= 1  # At least tried once


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


def test_quality_gate_forces_another_pass(monkeypatch):
    import agents.graph as g
    monkeypatch.setattr(g, "QUALITY_MIN", 9)              # require >=9; scorer always returns 8
    deps = _deps([HALF_SQL, GOOD_SQL], max_attempts=5)
    deps.quality_assessor = scripted_quality_assessor
    final = g.run_repair(_initial(), deps)
    assert final["attempts"] == 5                         # kept re-fixing until budget exhausted
    assert final["quality"].overall == 8                  # never reached the bar
    assert final["final_status"] == "healed"              # ships best-effort (pipeline IS fixed)


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
