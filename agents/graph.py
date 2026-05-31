"""Orchestration — the diagnose → fix → review → deploy → verify loop.

Design: the loop is implemented once as plain node functions + `run_repair` (no heavy
deps, fully testable with FakeGateway + scripted agents). `build_langgraph` wraps the
SAME node functions into a LangGraph StateGraph for the live, Weave-traced demo.

The agent *policies* (diagnoser/fixer/reviewer) are injected via `Deps`, so tests use
deterministic scripted policies and run_demo uses the LLM-backed ones from llm.py.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Callable

from agents.contracts import (
    Authorization,
    RepairState,
    VerifyResult,
)
from agents.trace import op

# Quality gate: 0 = off (scorecard is informational). Set e.g. QUALITY_MIN=9 to force
# another fix pass whenever the shipped SQL scores below 9 (i.e. 8 or below).
QUALITY_MIN = int(os.environ.get("QUALITY_MIN", "0"))


@dataclass
class Deps:
    gateway: object                  # ToolGateway (DirectGateway | FakeGateway)
    diagnoser: Callable              # (evidence: dict, state) -> (Diagnosis, TestSpec)
    fixer: Callable                  # (state, attempt_n: int) -> FixCandidate
    reviewer: Callable               # (state) -> ReviewVerdict
    max_attempts: int = 3
    quality_assessor: Callable = None  # (state) -> QualityScore | None (post-verify, non-gating)


# --------------------------------------------------------------------------- #
# Nodes — each a named @weave.op emitting typed milestone artifacts            #
# --------------------------------------------------------------------------- #
@op
def diagnose(state: RepairState, deps: Deps) -> dict:
    g = deps.gateway
    red = g.run_bruin(state["staging_asset_path"])          # reproduce → red (evidence)
    schema = g.inspect_schema(state["upstream_table"])      # localize structural cause
    evidence = {"error": (red["stderr"] or red["stdout"]).strip(), "schema": schema}
    diagnosis, test_spec = deps.diagnoser(evidence, state)
    return {"diagnosis": diagnosis, "test_spec": test_spec, "attempts": 0}


@op
def fix(state: RepairState, deps: Deps) -> dict:
    n = state.get("attempts", 0) + 1
    candidate = deps.fixer(state, n)
    return {"fix": candidate, "attempts": n}


@op
def review(state: RepairState, deps: Deps) -> dict:
    return {"review": deps.reviewer(state)}


def route_after_review(state: RepairState, deps: Deps) -> str:
    if state["review"].decision == "approve":
        return "deploy"
    if state.get("attempts", 0) >= deps.max_attempts:
        return "needs_human"
    return "fix"


@op
def deploy(state: RepairState, deps: Deps) -> dict:
    candidate = state["fix"]
    deps.gateway.apply_patch(state["staging_asset_path"], candidate.sql)
    auth = Authorization(
        decision="APPROVE",
        audit_id=uuid.uuid4().hex[:6],
        evidence=["review approved", "contracts preserved", f"attempt {candidate.attempt_n}"],
    )
    return {"authorization": auth}


@op
def verify(state: RepairState, deps: Deps) -> dict:
    g = deps.gateway
    res = g.run_bruin(state["staging_asset_path"], downstream=True)
    bruin_status = "green" if res["ok"] else "red"
    answer = g.oracle()
    oracle_status = "green" if (answer and answer.get("gross_revenue", 0) > 0) else "red"
    healed = bruin_status == "green" and oracle_status == "green"
    vr = VerifyResult(bruin_status=bruin_status, oracle_status=oracle_status,
                      healed=healed, oracle_answer=answer)
    return {"verify": vr, "final_status": "healed" if healed else "needs_human"}


@op
def assess_quality(state: RepairState, deps: Deps) -> dict:
    # Rate the proposed SQL (post-review, pre-deploy) on quality/maintainability.
    if not deps.quality_assessor:
        return {}
    return {"quality": deps.quality_assessor(state)}


def _quality_reject(state: dict, deps: Deps) -> bool:
    """True iff the gate is on, the score is below QUALITY_MIN, and attempts remain.

    Pure (no side-effects) so run_repair and the LangGraph adapter agree. When attempts
    are exhausted we ship the best-effort fix as `healed` — the pipeline IS fixed, and the
    residual is usually pre-existing style, not something the patch introduced — with the
    quality score left on record.
    """
    q = state.get("quality")
    if QUALITY_MIN <= 0 or not q or q.overall >= QUALITY_MIN:
        return False
    return state.get("attempts", 0) < deps.max_attempts


# --------------------------------------------------------------------------- #
# Driver 1 — plain Python loop (the testable source of truth)                  #
# --------------------------------------------------------------------------- #
@op
def run_repair(initial: dict, deps: Deps) -> dict:
    state: dict = dict(initial)
    state.update(diagnose(state, deps))
    while True:
        state.update(fix(state, deps))
        state.update(review(state, deps))
        nxt = route_after_review(state, deps)
        if nxt == "fix":
            continue
        if nxt == "needs_human":
            state["final_status"] = "needs_human"
            return state
        # approved on correctness → score quality BEFORE shipping
        state.update(assess_quality(state, deps))
        if _quality_reject(state, deps):
            continue  # correct but below the quality bar → another fix pass
        # cleared both gates → deploy + verify once
        state.update(deploy(state, deps))
        state.update(verify(state, deps))
        return state


# --------------------------------------------------------------------------- #
# Driver 2 — LangGraph adapter (same nodes; for the live, Weave-traced demo)   #
# --------------------------------------------------------------------------- #
def build_langgraph(deps: Deps):
    from langgraph.graph import END, StateGraph

    sg = StateGraph(RepairState)
    sg.add_node("diagnose", lambda s: diagnose(s, deps))
    sg.add_node("fix", lambda s: fix(s, deps))
    sg.add_node("review", lambda s: review(s, deps))
    sg.add_node("deploy", lambda s: deploy(s, deps))
    sg.add_node("verify", lambda s: verify(s, deps))
    sg.add_node("assess_quality", lambda s: assess_quality(s, deps))
    sg.add_node("needs_human", lambda s: {"final_status": "needs_human"})

    sg.set_entry_point("diagnose")
    sg.add_edge("diagnose", "fix")
    sg.add_edge("fix", "review")
    sg.add_conditional_edges(
        "review",
        lambda s: route_after_review(s, deps),
        {"deploy": "assess_quality", "fix": "fix", "needs_human": "needs_human"},
    )
    sg.add_conditional_edges(
        "assess_quality",
        lambda s: "fix" if _quality_reject(s, deps) else "deploy",
        {"fix": "fix", "deploy": "deploy"},
    )
    sg.add_edge("deploy", "verify")
    sg.add_edge("verify", END)
    sg.add_edge("needs_human", END)
    return sg.compile()
