"""Entrypoint — the live, Weave-traced self-healing demo.

  python -m agents.run_demo            # inject drift, heal, verify, reset
  python -m agents.run_demo --no-reset # leave the pipeline broken/fixed for inspection

Requires: ANTHROPIC_API_KEY, WANDB_API_KEY, a seeded duckdb.db, and bruin on PATH.
"""
from __future__ import annotations

import argparse
import subprocess

try:
    from dotenv import load_dotenv
    load_dotenv()  # pull keys from a local .env so you don't re-type them
except Exception:
    pass

from agents.contracts import (
    COLUMN_RENAME,
    MAX_ATTEMPTS,
    STAGING_ASSET_PATH,
    WEAVE_PROJECT,
)
from agents.graph import Deps, build_langgraph
from agents.trace import init as trace_init


def main(reset: bool = True, force_reject: bool = False) -> dict:
    trace_init(WEAVE_PROJECT)

    from agents import llm
    from agents.tools import DirectGateway

    gateway = DirectGateway()
    deps = Deps(
        gateway=gateway,
        diagnoser=llm.diagnoser,
        fixer=llm.fixer,  # Legacy single-fixer (kept for backward compatibility)
        fixer_pool=llm.fixer_pool,  # Best-of-n: parallel fixers
        picker=llm.picker,  # Best-of-n: selects winner from pool
        reviewer=llm.reviewer,
        max_attempts=MAX_ATTEMPTS,
        quality_assessor=llm.quality_assessor,
    )

    if force_reject:
        # Stage a realistic half-fix as attempt 1 (projection updated, JOIN still on
        # PAYMENT_TYPE) so the REAL Reviewer genuinely vetoes it -> guaranteed one
        # negotiation round, then the real Fixer corrects it on attempt 2.
        real_fixer = deps.fixer
        def _staged_fixer(state, attempt_n):
            if attempt_n == 1:
                from agents.contracts import FixCandidate
                current = open(state['staging_asset_path']).read()
                half = current.replace('PAYMENT_TYPE as payment_type',
                                       'PAYMENT_METHOD as payment_type', 1)
                return FixCandidate(attempt_n=1, sql=half,
                    summary='(staged) updated the projection but left the lookup JOIN on PAYMENT_TYPE',
                    files_changed=['zoomcamp/pipeline/assets/staging/trips.sql'], blast_radius='low')
            return real_fixer(state, attempt_n)
        deps.fixer = _staged_fixer
        print('\U0001f3ad  --force-reject: seeding a half-fix as attempt 1 (guarantees one Reviewer veto)')

    initial = {
        "staging_asset_path": str(STAGING_ASSET_PATH),
        "upstream_table": COLUMN_RENAME.upstream_table,
        "scenario": COLUMN_RENAME.name,
        "max_attempts": MAX_ATTEMPTS,
    }

    print(f"⛏  Injecting drift: {COLUMN_RENAME.inject_sql}")
    gateway.run_sql(COLUMN_RENAME.inject_sql)

    final: dict = {}
    try:
        app = build_langgraph(deps)
        final = app.invoke(initial)
        _summary(final)
    finally:
        if reset:
            print("↩  Resetting (reverting column + restoring staging SQL)")
            gateway.run_sql(COLUMN_RENAME.reset_sql)
            subprocess.run(["git", "checkout", "--", str(STAGING_ASSET_PATH)],
                           capture_output=True, text=True)
            gateway.run_bruin(str(STAGING_ASSET_PATH), downstream=True)  # rebuild reports (windowed)
        entity_project = WEAVE_PROJECT
        print(f"🔭  Weave trace: https://wandb.ai/{entity_project}")

    return final


def _summary(final: dict) -> None:
    status = final.get("final_status", "?")
    review = final.get("review")
    verify = final.get("verify")
    auth = final.get("authorization")
    print("\n=== Self-healing result ===")
    print(f"  status:   {status}")
    print(f"  attempts: {final.get('attempts')}")
    if review:
        print(f"  review:   {review.decision} (confidence {review.confidence})")
    if auth:
        print(f"  deployed: audit {auth.audit_id}")
    if verify:
        print(f"  verify:   bruin={verify.bruin_status} oracle={verify.oracle_status} "
              f"healed={verify.healed}")
        if verify.oracle_answer:
            print(f"  oracle:   {verify.oracle_answer}  (highest-grossing day)")
    quality = final.get("quality")
    if quality:
        print(f"  quality:  {quality.overall}/10 — {quality.rationale}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reset", action="store_true", help="leave the pipeline mutated")
    ap.add_argument("--force-reject", action="store_true",
                    help="seed a half-fix as attempt 1 to guarantee a visible Reviewer veto")
    args = ap.parse_args()
    main(reset=not args.no_reset, force_reject=args.force_reject)
