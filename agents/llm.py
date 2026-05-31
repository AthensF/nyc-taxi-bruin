"""LLM-backed agent policies (Claude via langchain-anthropic).

These are the production diagnoser/fixer/reviewer injected into graph.Deps for the live
run. Tests use scripted policies instead, so this module's heavy imports stay lazy.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from agents.contracts import (
    AGENT_MODEL,
    COLUMN_RENAME,
    Diagnosis,
    FixCandidate,
    QualityScore,
    ReviewVerdict,
    TestSpec,
)
from agents.prompts import DIAGNOSER_SYS, FIXER_SYS, QUALITY_SYS, REVIEWER_SYS
from agents.trace import op

# Per-role models — weak proposer, strong critic by default; all env-overridable.
# Set AGENT_MODEL to make every role uniform (the reliable stage-run setting).
DIAGNOSER_MODEL = os.environ.get("DIAGNOSER_MODEL", AGENT_MODEL)
FIXER_MODEL = os.environ.get("FIXER_MODEL") or os.environ.get("AGENT_MODEL") or "claude-haiku-4-5-20251001"
REVIEWER_MODEL = os.environ.get("REVIEWER_MODEL", AGENT_MODEL)


def _chat(system: str, user: str, model: str = AGENT_MODEL) -> str:
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model=model, max_tokens=2048, temperature=0)
    return llm.invoke([("system", system), ("human", user)]).content


def _read(path: str) -> str:
    return Path(path).read_text()


def _kv(text: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _list(text: str, key: str) -> list:
    block = re.search(rf"^{key}:\s*\n((?:\s*[-*].+\n?)+)", text, re.IGNORECASE | re.MULTILINE)
    if not block:
        one = _kv(text, key)
        return [one] if one and one.lower() != "none" else []
    return [ln.strip(" -*\t") for ln in block.group(1).splitlines() if ln.strip(" -*\t")]


def _fenced_sql(text: str) -> str:
    m = re.search(r"```sql\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


@op
def diagnoser(evidence: dict, state: dict):
    current = _read(state["staging_asset_path"])
    user = (
        f"Error:\n{evidence.get('error', '')}\n\n"
        f"Upstream schema:\n{evidence.get('schema')}\n\n"
        f"Current staging SQL:\n{current}"
    )
    txt = _chat(DIAGNOSER_SYS, user, DIAGNOSER_MODEL)
    diagnosis = Diagnosis(
        root_cause=_kv(txt, "ROOT_CAUSE") or "upstream schema drift",
        why=_kv(txt, "WHY"),
        evidence=[evidence.get("error", "")[:200]],
        references=list(COLUMN_RENAME.references_to_update),
        acceptance_criteria=_list(txt, "ACCEPTANCE_CRITERIA") or [
            "remove all references to the old column",
            "preserve payment_type_name",
            "preserve all not_null checks",
            "highest-grossing-day oracle returns a valid row",
        ],
    )
    return diagnosis, TestSpec(structural_assertions=[
        "no bare payment_type reference", "references payment_method", "oracle returns a valid row",
    ])


@op
def fixer(state: dict, attempt_n: int) -> FixCandidate:
    current = _read(state["staging_asset_path"])
    feedback = state["review"].rationale if state.get("review") else ""
    q = state.get("quality")
    if q and getattr(q, "overall", 0):
        feedback += f"  [prior quality {q.overall}/10 — improve: {q.rationale}]"
    user = (
        f"Diagnosis: {state['diagnosis'].root_cause}\n"
        f"Acceptance criteria: {state['diagnosis'].acceptance_criteria}\n"
        f"Prior reviewer feedback (if any): {feedback}\n\n"
        f"Current staging SQL:\n{current}\n\n"
        "Return the FULL corrected file in one ```sql fence."
    )
    txt = _chat(FIXER_SYS, user, FIXER_MODEL)
    return FixCandidate(
        attempt_n=attempt_n,
        sql=_fenced_sql(txt) or current,
        summary=_kv(txt, "SUMMARY") or "patched staging SQL",
        files_changed=["zoomcamp/pipeline/assets/staging/trips.sql"],
        blast_radius=_kv(txt, "BLAST_RADIUS") or "low",
    )


@op
def reviewer(state: dict) -> ReviewVerdict:
    user = (
        f"Acceptance criteria:\n{state['diagnosis'].acceptance_criteria}\n\n"
        f"Proposed SQL:\n{state['fix'].sql}"
    )
    txt = _chat(REVIEWER_SYS, user, REVIEWER_MODEL)
    raw = (_kv(txt, "DECISION") or "reject").lower()
    decision = "approve" if raw.startswith("approve") else "reject"
    try:
        confidence = float(_kv(txt, "CONFIDENCE") or 0)
    except ValueError:
        confidence = 0.0
    return ReviewVerdict(
        decision=decision,
        confidence=confidence,
        rationale=_kv(txt, "RATIONALE") or txt[:200],
        correctness_findings=_list(txt, "CORRECTNESS"),
        quality_findings=_list(txt, "QUALITY"),
    )


def _scored(raw: str) -> dict:
    m = re.match(r"\s*(\d+)\s*[-:]\s*(.*)", raw)
    return {"score": int(m.group(1)), "note": m.group(2).strip()} if m else {"score": 0, "note": raw}


def _int(raw: str) -> int:
    m = re.search(r"\d+", raw or "")
    return int(m.group()) if m else 0


@op
def quality_assessor(state: dict) -> QualityScore:
    user = (
        f"Acceptance criteria:\n{state['diagnosis'].acceptance_criteria}\n\n"
        f"Deployed SQL:\n{state['fix'].sql}"
    )
    txt = _chat(QUALITY_SYS, user, REVIEWER_MODEL)
    criteria = {
        k.lower(): _scored(_kv(txt, k))
        for k in ("CORRECTNESS_CONTRACT", "MINIMALITY", "READABILITY", "NO_DEAD_CODE", "ROBUSTNESS")
    }
    return QualityScore(
        overall=_int(_kv(txt, "OVERALL")),
        rationale=_kv(txt, "REASON") or txt[:200],
        criteria=criteria,
    )
