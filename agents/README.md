# Self-Healing ELT Demo Runbook

A one-screen runbook for the Weave-traced self-healing ELT demo.

## Prerequisites

- Python environment with `agents/requirements.txt` installed.
- `WANDB_API_KEY` exported for Weave logging.
- `ANTHROPIC_API_KEY` exported for the live agent demo.
- `duckdb.db` seeded, with `bruin` and `duckdb` available on `PATH`.
- Part A reset/inject scripts present; Part B agent implementation committed or available in the worktree.

## Commands

```bash
python scripts/weave_smoke.py
bash scripts/seed.sh
bash scripts/reset.sh
python -m agents.run_demo
```

Use `python -m agents.run_demo --no-reset` only when you intentionally want to inspect the patched SQL before resetting.

## Weave trace checklist

Open `https://wandb.ai/afitzc-mit/self-healing-elt` after the smoke check or live demo.

Confirm the demo trace reads like an incident timeline, not a chain-of-thought dump:

- **Smoke:** `part_c_smoke_check` logs under `afitzc-mit/self-healing-elt`.
- **Root trace:** `run_repair` or the LangGraph run frames the incident.
- **Diagnosis:** `diagnose` includes the red `run_bruin` evidence and `inspect_schema` showing `payment_method`.
- **Fix proposal:** `fix` returns a `FixCandidate` with file, summary, blast radius, and criteria.
- **Review loop:** `review` shows reject/approve, rationale, findings, and confidence.
- **World tools:** `run_bruin`, `inspect_schema`, `oracle`, and `apply_patch` appear as tool/signal spans.
- **Deploy:** `deploy` returns an `Authorization` with `APPROVE`, evidence, and `audit_id`.
- **Proof:** `verify` shows Bruin green plus oracle green with the highest-grossing-day row.

Part B is still being implemented, so check final span names before demo day and update the run-of-show if they shift.
