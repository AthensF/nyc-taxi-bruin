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


def main(reset: bool = True) -> dict:
    trace_init(WEAVE_PROJECT)

    from agents import llm
    from agents.tools import DirectGateway

    gateway = DirectGateway()
    deps = Deps(gateway=gateway, diagnoser=llm.diagnoser, fixer=llm.fixer,
                reviewer=llm.reviewer, max_attempts=MAX_ATTEMPTS)

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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reset", action="store_true", help="leave the pipeline mutated")
    args = ap.parse_args()
    main(reset=not args.no_reset)
